import SwiftUI
import JarvisKit

/// Sign-in, as jm/SignIn/SignIn.tsx and screens/Login.tsx draw it: Google
/// first, PIN sign-in behind a link, a row per person, then the PIN.
///
/// The web's "Operator access" link is not here: the server never signs a
/// phone in on the operator passphrase (app/devices.py). In its place is the
/// way back to the address screen, which the web does not need.
struct SignInScreen: View {
    @EnvironmentObject private var model: AppModel

    @State private var people: [PersonChoice]?
    @State private var googleOnly = false
    @State private var pinOpen: Bool
    @State private var who: String?
    @State private var pin = ""
    @State private var reveal = false
    @State private var busy = false
    @State private var error: String?

    /// Previews open on a later step.
    init(pinOpen: Bool = false, who: String? = nil) {
        _pinOpen = State(initialValue: pinOpen)
        _who = State(initialValue: who)
    }

    private var complete: Bool { PinFormat.isComplete(pin) }

    private var subtitle: String {
        if pinOpen { return who == nil ? "Choose who you are." : "Enter your PIN to continue." }
        return googleOnly ? "Continue with Google." : "Continue with Google, or sign in with a PIN."
    }

    var body: some View {
        ScrollView {
            if let people {
                column(people)
                    .frame(maxWidth: JM.Size.signIn)
                    .padding(.horizontal, JM.Space.s4)
                    .padding(.vertical, JM.Space.s12)
                    .frame(maxWidth: .infinity)
            }
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .background(Color.jmBg.ignoresSafeArea())
        .task { await load() }
    }

    @ViewBuilder
    private func column(_ people: [PersonChoice]) -> some View {
        VStack(spacing: JM.Space.s3) {
            VStack(spacing: JM.Space.s2) {
                Text("Jarvis")
                    .jmText(.display)
                    .foregroundStyle(Color.jmFg)
                    .accessibilityAddTraits(.isHeader)
                Text(subtitle)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.bottom, JM.Space.s5)

            // One primary at a time: once the PIN form is open, its Continue is.
            JMButton("Continue with Google", kind: pinOpen ? .secondary : .primary, wide: true, tall: true) {
                Task { await google() }
            }
            .disabled(busy)

            if !googleOnly {
                link(pinOpen ? "Hide PIN sign-in" : "Sign in with PIN", style: .label, color: .jmAccentText) {
                    pinOpen.toggle()
                    pin = ""
                    error = nil
                }
            }

            if pinOpen && !people.isEmpty {
                VStack(spacing: JM.Space.s2) {
                    ForEach(people) { person in
                        personRow(person)
                    }
                }
                .padding(.top, JM.Space.s2)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Who is signing in")
            }

            if pinOpen {
                VStack(spacing: JM.Space.s3) {
                    ZStack(alignment: .trailing) {
                        JMField(
                            "PIN", text: $pin, large: true, secure: !reveal, centered: true,
                            spacing: pin.isEmpty ? 0 : 1.8, inset: JM.Space.s12
                        ) {
                            Task { await submit() }
                        }
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(.password)
                        .submitLabel(.go)
                        .onChange(of: pin) { _, typed in
                            let formatted = PinFormat.format(typed)
                            if formatted != typed { pin = formatted }
                        }
                        JMButton(icon: reveal ? .eyeOff : .eye, kind: .ghost, size: .sm, label: reveal ? "Hide PIN" : "Show PIN") {
                            reveal.toggle()
                        }
                        .padding(.trailing, JM.Space.s2)
                    }
                    JMButton(busy ? "Checking…" : "Continue", kind: .primary, wide: true, tall: true) {
                        Task { await submit() }
                    }
                    .disabled(busy || !complete || who == nil)
                    if let error {
                        ErrorLine(error)
                    }
                }
                .padding(.top, JM.Space.s2)
            }

            if !pinOpen, let error {
                ErrorLine(error)
            }

            link("Use a different address", style: .caption, color: .jmFgSecondary) {
                Task { await model.forgetOrigin() }
            }
            .padding(.top, JM.Space.s4)
        }
    }

    private func link(_ title: String, style: JMTextStyle, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .jmText(style)
                .foregroundStyle(color)
                .padding(.horizontal, JM.Space.s1)
                .padding(.vertical, 2)
        }
        .buttonStyle(JMPlainStyle())
    }

    private func personRow(_ person: PersonChoice) -> some View {
        let on = person.personKey == who
        let role = Wording.titleOnly(person.title)
        return Button {
            who = person.personKey
        } label: {
            HStack(spacing: JM.Space.s3) {
                Avatar(name: person.name)
                VStack(alignment: .leading, spacing: 0) {
                    Text(person.name).jmText(.label).foregroundStyle(Color.jmFg).lineLimit(1)
                    if !role.isEmpty {
                        Text(role).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if on {
                    Icon(.check, size: 18).foregroundStyle(Color.jmAccentText)
                }
            }
            .padding(.horizontal, JM.Space.s3)
            .padding(.vertical, JM.Space.s2)
            .frame(minHeight: JM.Size.touch)
            .background(on ? Color.jmAccentSoft : Color.clear, in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                    .strokeBorder(on ? Color.jmAccent : Color.jmBorder, lineWidth: 1))
        }
        .buttonStyle(JMPlainStyle())
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func load() async {
        let methods = await model.client.authMethods()
        let roster = (try? await model.client.people()) ?? []
        googleOnly = methods.googleOnly
        // With exactly one person there is nobody to choose between.
        if roster.count == 1 { who = roster[0].personKey }
        if let status = model.status {
            error = status
            model.status = nil
        }
        people = roster
    }

    private func google() async {
        if busy { return }
        error = nil
        busy = true
        defer { busy = false }
        if let problem = await model.signInWithGoogle() {
            error = problem
        }
    }

    private func submit() async {
        guard !busy, complete, let who else { return }
        error = nil
        busy = true
        defer { busy = false }
        do {
            try await model.signIn(personKey: who, pin: pin)
        } catch let problem as JarvisError {
            switch problem {
            case .http(401, _):
                error = "That PIN is not right."
            case .http(429, _):
                error = "Too many attempts. Wait a few minutes and try again."
            case .http(_, let message):
                error = message
            default:
                error = "Something went wrong. Try again."
            }
            pin = ""
        } catch {
            self.error = "Could not reach Jarvis."
            pin = ""
        }
    }
}
