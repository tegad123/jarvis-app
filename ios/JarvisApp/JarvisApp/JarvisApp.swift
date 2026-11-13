import SwiftUI
import JarvisKit

@main
struct JarvisApp: App {
    @StateObject private var model = AppModel()

    init() {
        Platform.registerFonts()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}

/// What the whole app shares: the tenant address, the client, who is signed
/// in, and the light or dark choice.
@MainActor
final class AppModel: ObservableObject {
    @Published var originText: String
    @Published var session: SessionInfo?
    /// A sentence for the first screen, such as a deletion receipt.
    @Published var status: String?
    /// The web's theme switch: dark unless the person chose light.
    @Published var light: Bool {
        didSet { UserDefaults.standard.set(light ? "light" : "dark", forKey: Self.themeKey) }
    }
    let client: JarvisClient

    init() {
        let saved = UserDefaults.standard.string(forKey: Self.originKey) ?? ""
        originText = saved
        light = UserDefaults.standard.string(forKey: Self.themeKey) == "light"
        let consent = UserDefaults.standard.bool(forKey: Self.consentKey)
        client = JarvisClient(transport: URLSessionTransport(), aiConsent: consent)
        if let parsed = try? Self.apply(saved, to: client) { originText = parsed }
    }

    /// For previews: a client that is already pointed somewhere, and
    /// optionally someone already signed in.
    init(client: JarvisClient, session: SessionInfo?, light: Bool = false) {
        self.client = client
        self.session = session
        self.light = light
        originText = client.origin?.url.absoluteString ?? ""
    }

    var hasOrigin: Bool { client.origin != nil }

    var role: String { session?.role ?? "member" }
    var personKey: String? { session?.personKey }
    var isManager: Bool { Roles.canManageTeam(session?.role) }

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

    func signOut() async {
        await client.logout()
        session = nil
    }

    /// PIN sign-in, as the web does it: POST /api/login, then the session.
    func signIn(personKey: String, pin: String) async throws {
        _ = try await client.login(personKey: personKey, pin: pin)
        session = try await client.session()
    }

    /// Google sign-in through the system sheet (app/devices.py): the tenant's
    /// own page signs the person in and hands back a one-time code, which is
    /// traded for this phone's tokens. Returns a sentence when it fails and
    /// nil when it worked or the person closed the sheet.
    func signInWithGoogle() async -> String? {
        let proof = PKCE()
        do {
            let start = try client.appStartURL(challenge: proof.challenge)
            let callback = try await Platform.webSignIn(url: start, callbackScheme: JarvisClient.appCallbackScheme)
            guard let code = PKCE.code(fromCallback: callback) else { return Self.googleFailed }
            try await client.exchange(code: code, verifier: proof.verifier, deviceName: Platform.deviceName)
            session = try await client.session()
            return nil
        } catch JarvisError.cancelled {
            return nil
        } catch let error as JarvisError {
            if error.isUnauthorized { return Self.googleFailed }
            return error.serverMessage ?? "Something went wrong. Try again."
        } catch {
            return "Could not reach Jarvis."
        }
    }

    /// The sentence to show for a failed call: the server's own when it sent
    /// one, else `fallback`. A 401 signs the person out and returns nil,
    /// because the sign-in screen is the message.
    func words(for error: Error, fallback: String) -> String? {
        guard let known = error as? JarvisError else { return "Could not reach Jarvis." }
        if known.isUnauthorized {
            session = nil
            return nil
        }
        switch known {
        case .http(_, let message):
            // JarvisKit's own stand-in for a reply with no sentence in it.
            return message.isEmpty || message.hasPrefix("The server answered") ? fallback : message
        case .consentRequired:
            return "Turn on Ask in Settings before sending a message."
        case .cancelled:
            return nil
        case .noOrigin, .badResponse:
            return fallback
        }
    }

    /// For a list that could not load: the screen's own sentence, and under
    /// it the server's or the status. Nil after a 401, which signs out.
    func problem(for error: Error, message: String, offline: String? = nil) -> LoadProblem? {
        guard let known = error as? JarvisError else {
            return LoadProblem(message: offline ?? message, detail: nil)
        }
        if known.isUnauthorized {
            session = nil
            return nil
        }
        guard case .http(let status, let sentence) = known else {
            return LoadProblem(message: message, detail: nil)
        }
        let plain = sentence.isEmpty || sentence.hasPrefix("The server answered")
        return LoadProblem(message: message, detail: plain ? "HTTP \(status)" : sentence)
    }

    static let googleFailed = "That Google account isn't on this Jarvis. Ask the owner to add your email, or try another account."

    private static let originKey = "jarvis.tenantOrigin"
    private static let consentKey = "jarvis.aiConsent"
    private static let themeKey = "jarvis-mono-theme"

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

/// What an ErrorState shows when a screen's data did not arrive.
struct LoadProblem: Equatable {
    var message: String
    var detail: String?
}
