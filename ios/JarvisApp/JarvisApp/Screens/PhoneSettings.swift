import SwiftUI
import JarvisKit

/// The parts of Settings that exist only on the phone. The web has no twin
/// for these: the App Store asks for the AI notice and for a way to close an
/// account from inside the app, and only a phone has to remember which
/// Jarvis it talks to. They use the same cards as the rest of Settings.
struct PhoneSettings: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        AskConsentSection()
        SignInClosureSection()
        if model.role == "owner" {
            TeamDeletionSection()
        }
        SettingsSection(
            title: "This phone",
            description: "The Jarvis this phone talks to. Using a different address signs you out here.",
            rows: [SettingsRow(id: "address", title: model.client.origin?.host ?? model.originText)],
            actions: {
                JMButton("Use a different address") {
                    Task { await model.forgetOrigin() }
                }
            })
    }
}

/// Whether Ask messages typed on this phone may go to the model.
private struct AskConsentSection: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let allowed = model.consent
        SettingsSection(
            title: "Ask Jarvis on this phone",
            description: AIDisclosure.text,
            status: StatusCopy(tone: allowed ? .done : .neutral, label: allowed ? "Allowed" : "Not allowed"),
            actions: {
                JMButton(allowed ? "Stop allowing" : "Allow", kind: allowed ? .secondary : .primary) {
                    model.consent = !allowed
                }
            })
    }
}

/// Turns off the caller's own sign-in and records a deletion request.
private struct SignInClosureSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var pin = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        SettingsSection(
            title: "Your sign-in",
            description: "This turns off your sign-in and records a deletion request. It does not delete leads, tasks, or messages, and it does not delete the team. The only owner cannot do this to themselves.",
            error: error,
            content: {
                JMField("Your PIN", text: $pin, secure: true)
                    .keyboardType(.numberPad)
                JMButton(busy ? "Turning off…" : "Turn off my sign-in") { Task { await close() } }
                    .disabled(pin.isEmpty || busy)
            })
    }

    private func close() async {
        busy = true
        error = nil
        defer {
            busy = false
            pin = ""
        }
        do {
            let receipt = try await model.client.requestAccountDeletion(pin: pin)
            model.status = receipt.detail
            await model.signOut()
        } catch let known as JarvisError {
            // A wrong PIN is a 401 here; it must not read as a signed-out session.
            error = known.serverMessage ?? "That request did not go through."
        } catch {
            self.error = "That request did not go through."
        }
    }
}

/// Deletes the whole team. Owner only; the server asks for the PIN, a typed
/// phrase and a one-time nonce, and refuses anyone else.
private struct TeamDeletionSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var impact: TeamDeletionImpact?
    @State private var typed = ""
    @State private var pin = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        SettingsSection(
            title: "Delete this team",
            description: "This permanently deletes this team’s records stored in Jarvis: every member’s sign-in, leads, tasks, chats, and messages. It is not the same as turning off your own sign-in.",
            error: error,
            content: {
                if let impact {
                    lines(impact.removes, color: .jmFg)
                    lines(impact.notRemoved, color: .jmFgSecondary)
                    Text("Type \(impact.phrase) to confirm. There is no one-tap delete.")
                        .jmText(.bodySm)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    JMField(impact.phrase, text: $typed)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityLabel("Confirmation phrase")
                    JMField("Owner PIN", text: $pin, secure: true)
                        .keyboardType(.numberPad)
                    JMButton(busy ? "Deleting…" : "Delete this team") { Task { await delete(impact) } }
                        .disabled(busy || (impact.nonce ?? "").isEmpty || pin.isEmpty || typed != impact.phrase)
                } else if error == nil {
                    LoadingLine()
                }
            })
        .task { await load() }
    }

    @ViewBuilder
    private func lines(_ items: [String], color: Color) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: JM.Space.s1) {
                ForEach(items.numbered) { item in
                    Text(item.value)
                        .jmText(.bodySm)
                        .foregroundStyle(color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func load() async {
        do {
            impact = try await model.client.teamDeletionChallenge()
        } catch {
            self.error = model.words(for: error, fallback: "Could not load team deletion.")
        }
    }

    private func delete(_ impact: TeamDeletionImpact) async {
        busy = true
        error = nil
        defer {
            busy = false
            pin = ""
        }
        do {
            let receipt = try await model.client.deleteTeam(pin: pin, phrase: typed, nonce: impact.nonce ?? "")
            model.status = ([receipt.detail] + receipt.stillExists).joined(separator: " ")
            await model.signOut()
        } catch let known as JarvisError {
            // The nonce works once: a refusal needs a fresh one.
            let said = known.serverMessage ?? "That request did not go through. Nothing here was treated as deleted."
            self.impact = nil
            await load()
            error = said
        } catch {
            self.error = "That request did not go through. Nothing here was treated as deleted."
        }
    }
}
