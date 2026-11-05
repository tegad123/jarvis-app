import SwiftUI
import JarvisKit

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if !model.hasOrigin {
                SetupView()
            } else if model.session == nil {
                LoginView()
            } else {
                MainView()
            }
        }
        .tint(.primary)
    }
}

struct SetupView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Jarvis on this phone talks to one tenant. Paste that tenant's HTTPS address. Nothing is filled in for you.")
                        .font(.body)
                }
                Section("Tenant") {
                    TextField("https://", text: $model.originText)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .accessibilityLabel("Tenant HTTPS address")
                    Button("Use this address") { model.saveOrigin() }
                        .accessibilityHint("Checks the address and connects")
                }
                if let status = model.status {
                    Section { Text(status).foregroundStyle(.red).accessibilityLabel(status) }
                }
                Section("Not set yet") {
                    Text("The bundle id and Apple team in this project are development placeholders. Signing, the store listing, and which login the store build offers are still open.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Connect")
        }
    }
}

struct LoginView: View {
    @EnvironmentObject private var model: AppModel
    @State private var people: [PersonChoice] = []
    @State private var personKey = ""
    @State private var pin = ""
    @State private var busy = false
    @State private var error: String?
    @State private var loadingPeople = true

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Sign in with your PIN. Google and the operator passphrase are not on this screen.")
                        .font(.body)
                }
                if loadingPeople {
                    ProgressView("Loading the team")
                } else if people.isEmpty {
                    TextField("Person", text: $personKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityLabel("Person key")
                } else {
                    Picker("Who", selection: $personKey) {
                        Text("Choose").tag("")
                        ForEach(people) { person in
                            Text(person.name).tag(person.personKey)
                        }
                    }
                    .accessibilityLabel("Who is signing in")
                }
                SecureField("PIN", text: $pin)
                    .accessibilityLabel("PIN")
                if let error {
                    Text(error).foregroundStyle(.red)
                }
                Button(busy ? "Signing in…" : "Sign in") { Task { await signIn() } }
                    .disabled(busy || pin.isEmpty || (people.isEmpty ? personKey.isEmpty : personKey.isEmpty))
                Button("Different tenant") { Task { await model.forgetOrigin() } }
            }
            .navigationTitle("Sign in")
            .task { await loadPeople() }
        }
    }

    private func loadPeople() async {
        loadingPeople = true
        defer { loadingPeople = false }
        do { people = try await model.client.people() }
        catch { people = [] }
    }

    private func signIn() async {
        busy = true
        defer { busy = false; pin = "" }
        do {
            _ = try await model.client.login(personKey: personKey, pin: pin)
            model.session = try await model.client.session()
            error = nil
        } catch JarvisError.http(_, let message) {
            error = message
        } catch {
            error = "Could not sign in."
        }
    }
}

struct MainView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TabView {
            AskView().tabItem { Label("Ask", systemImage: "bubble.left") }
            LeadsView().tabItem { Label("Leads", systemImage: "person.2") }
            TasksView().tabItem { Label("Tasks", systemImage: "checklist") }
            ApprovalsView().tabItem { Label("Approvals", systemImage: "checkmark.seal") }
            SettingsView().tabItem { Label("Settings", systemImage: "gear") }
        }
    }
}
