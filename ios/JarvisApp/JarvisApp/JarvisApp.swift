import SwiftUI
import JarvisKit

@main
struct JarvisApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var originText: String
    @Published var client: JarvisClient
    @Published var session: SessionInfo?
    @Published var status: String?

    init() {
        let saved = UserDefaults.standard.string(forKey: Self.originKey) ?? ""
        originText = saved
        let consent = UserDefaults.standard.bool(forKey: Self.consentKey)
        client = JarvisClient(transport: URLSessionTransport(), aiConsent: consent)
        if let parsed = try? apply(saved, to: client) { originText = parsed }
    }

    var hasOrigin: Bool { client.origin != nil }
    var consent: Bool {
        get { client.aiConsent }
        set {
            objectWillChange.send()
            client.aiConsent = newValue
            UserDefaults.standard.set(newValue, forKey: Self.consentKey)
        }
    }

    func saveOrigin() {
        do {
            originText = try Self.apply(originText, to: client)
            UserDefaults.standard.set(originText, forKey: Self.originKey)
            session = nil
            status = nil
        } catch let error as OriginError {
            status = Self.originMessage(error)
        } catch {
            status = "That address could not be used."
        }
    }

    func forgetOrigin() async {
        await client.logout()
        client.clearOrigin()
        session = nil
        originText = ""
        UserDefaults.standard.removeObject(forKey: Self.originKey)
    }

    private static let originKey = "jarvis.tenantOrigin"
    private static let consentKey = "jarvis.aiConsent"

    @discardableResult
    private static func apply(_ raw: String, to client: JarvisClient) throws -> String {
        try client.useOrigin(raw)
        return client.origin?.url.absoluteString ?? raw
    }

    private static func originMessage(_ error: OriginError) -> String {
        switch error {
        case .empty: return "Enter the tenant's HTTPS address."
        case .notHTTPS: return "The address has to start with https://."
        case .missingHost: return "That address has no host."
        case .credentialsInURL: return "Don't put a name or password in the address."
        case .hasQueryOrFragment: return "Use only the site address, with no ? or #."
        }
    }
}
