import SwiftUI
import JarvisKit

// Everything that needs UIKit or another iOS framework lives in this file,
// so the screens themselves are plain SwiftUI: font registration, the system
// sign-in sheet, the photo and file pickers, the pasteboard, opening a link.

/// A photo or document the person picked, ready to upload.
struct PickedFile {
    var filename: String
    var mime: String
    var data: Data
}

/// Matches the server's per-file cap, so the app can refuse before uploading.
let maxUploadBytes = 10 * 1024 * 1024

#if canImport(UIKit)
import UIKit
import CoreText
import AuthenticationServices
import PhotosUI
import QuickLook
import UniformTypeIdentifiers

enum Platform {
    /// Registers the bundled Geist files. Without them text falls back to
    /// the system font and nothing else changes.
    static func registerFonts() {
        for name in ["Geist-Regular", "Geist-Medium", "Geist-SemiBold"] {
            guard let url = Bundle.main.url(forResource: name, withExtension: "ttf") else { continue }
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }

    /// The name the server lists this phone under.
    @MainActor static var deviceName: String {
        UIDevice.current.name
    }

    @MainActor static func open(_ url: URL) {
        UIApplication.shared.open(url, options: [:], completionHandler: nil)
    }

    static func copy(_ text: String) {
        UIPasteboard.general.string = text
    }

    /// The web re-encodes every photo to a JPEG no longer than 2000px on its
    /// long edge (lib/attachments.ts); so does the phone. Nil when the data
    /// is not an image the phone can read.
    static func preparedPhoto(_ data: Data, maxEdge: CGFloat = 2000, quality: CGFloat = 0.85) -> Data? {
        guard let image = UIImage(data: data) else { return nil }
        let longest = max(image.size.width, image.size.height)
        guard longest > 0 else { return nil }
        let scale = min(1, maxEdge / longest)
        let size = CGSize(width: (image.size.width * scale).rounded(), height: (image.size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        return renderer.jpegData(withCompressionQuality: quality) { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }

    /// Opens the tenant's own sign-in page in the system sheet and waits for
    /// it to come back to the app's scheme. Throws `JarvisError.cancelled`
    /// when the person closes the sheet.
    @MainActor static func webSignIn(url: URL, callbackScheme: String) async throws -> URL {
        let flow = WebSignIn()
        return try await flow.run(url: url, callbackScheme: callbackScheme)
    }
}

/// One run of the system sign-in sheet. The window is looked up before the
/// sheet starts, so the delegate callback only hands back a stored value.
private final class WebSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    private var anchor: ASPresentationAnchor?
    private var continuation: CheckedContinuation<URL, Error>?
    private let lock = NSLock()

    @MainActor func run(url: URL, callbackScheme: String) async throws -> URL {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let windows = scenes.flatMap { $0.windows }
        anchor = windows.first { $0.isKeyWindow } ?? windows.first ?? ASPresentationAnchor()

        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
            self.continuation = continuation
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { callback, error in
                if let callback {
                    self.finish(.success(callback))
                } else if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                    self.finish(.failure(JarvisError.cancelled))
                } else {
                    self.finish(.failure(error ?? JarvisError.badResponse))
                }
            }
            session.presentationContextProvider = self
            self.session = session
            if !session.start() {
                self.finish(.failure(JarvisError.badResponse))
            }
        }
    }

    /// Resumes the waiting sign-in once, whichever callback gets here first.
    private func finish(_ result: Result<URL, Error>) {
        lock.lock()
        let waiting = continuation
        continuation = nil
        lock.unlock()
        waiting?.resume(with: result)
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // Set in run(), before the sheet starts.
        anchor!
    }
}

/// The composer's plus button: a photo from the library or a file.
struct AttachmentPicker<Label: View>: View {
    var disabled: Bool
    var onPick: (PickedFile) -> Void
    var onProblem: (String) -> Void
    @ViewBuilder var label: () -> Label

    @State private var showPhotos = false
    @State private var showFiles = false
    @State private var photo: PhotosPickerItem?

    /// What the server stores: its image and document allowlist.
    private static var fileTypes: [UTType] {
        let named = [
            "com.microsoft.word.doc",
            "org.openxmlformats.wordprocessingml.document",
            "com.microsoft.excel.xls",
            "org.openxmlformats.spreadsheetml.sheet",
        ].compactMap { UTType($0) }
        return [.jpeg, .png, .gif, .webP, .heic, .pdf, .plainText, .commaSeparatedText] + named
    }

    var body: some View {
        Menu {
            Button("Photo library") { showPhotos = true }
            Button("Choose a file") { showFiles = true }
        } label: {
            label()
        }
        .disabled(disabled)
        .accessibilityLabel("Add a photo or file")
        .photosPicker(isPresented: $showPhotos, selection: $photo, matching: .images)
        .fileImporter(isPresented: $showFiles, allowedContentTypes: Self.fileTypes) { result in
            guard case .success(let url) = result else { return }
            readFile(url)
        }
        .onChange(of: photo) { _, item in
            guard let item else { return }
            photo = nil
            Task { await readPhoto(item) }
        }
    }

    private func readPhoto(_ item: PhotosPickerItem) async {
        guard let raw = try? await item.loadTransferable(type: Data.self),
              let jpeg = Platform.preparedPhoto(raw) else {
            onProblem("That photo could not be read.")
            return
        }
        deliver(PickedFile(filename: "photo.jpg", mime: "image/jpeg", data: jpeg))
    }

    private func readFile(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let raw = try? Data(contentsOf: url) else {
            onProblem("That file could not be read.")
            return
        }
        let type = UTType(filenameExtension: url.pathExtension)
        if type?.conforms(to: .image) == true, let jpeg = Platform.preparedPhoto(raw) {
            let base = url.deletingPathExtension().lastPathComponent
            deliver(PickedFile(filename: "\(base).jpg", mime: "image/jpeg", data: jpeg))
            return
        }
        deliver(PickedFile(
            filename: url.lastPathComponent,
            mime: type?.preferredMIMEType ?? "application/octet-stream",
            data: raw))
    }

    private func deliver(_ file: PickedFile) {
        if file.data.count > maxUploadBytes {
            onProblem("That file is over 10 MB.")
        } else {
            onPick(file)
        }
    }
}

extension View {
    /// Shows a stored file full screen: the phone's version of the web
    /// opening an attachment in a new tab.
    func jmFilePreview(_ url: Binding<URL?>) -> some View {
        quickLookPreview(url)
    }
}

/// A picture fetched from the tenant with this session, such as a thumbnail.
struct DataImage: View {
    var data: Data

    var body: some View {
        if let image = UIImage(data: data) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            Color.jmPanelRaised
        }
    }
}

#else

// The Linux type-check has no UIKit. These stand in so the screens compile;
// they are never part of an iOS build.
enum Platform {
    static func registerFonts() {}
    @MainActor static var deviceName: String { "iPhone" }
    @MainActor static func open(_ url: URL) {}
    static func copy(_ text: String) {}
    static func preparedPhoto(_ data: Data) -> Data? { nil }
    @MainActor static func webSignIn(url: URL, callbackScheme: String) async throws -> URL {
        throw JarvisError.cancelled
    }
}

struct AttachmentPicker<Label: View>: View {
    var disabled: Bool
    var onPick: (PickedFile) -> Void
    var onProblem: (String) -> Void
    @ViewBuilder var label: () -> Label

    var body: some View {
        label()
    }
}

extension View {
    func jmFilePreview(_ url: Binding<URL?>) -> some View {
        self
    }
}

struct DataImage: View {
    var data: Data

    var body: some View {
        Color.jmPanelRaised
    }
}

#endif
