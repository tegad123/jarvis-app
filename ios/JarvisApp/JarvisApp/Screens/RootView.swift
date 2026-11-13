import SwiftUI
import JarvisKit

/// Which of the three states the app is in: no address yet, signed out, or in.
struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            Color.jmBg.ignoresSafeArea()
            if !model.hasOrigin {
                ConnectScreen()
            } else if model.session == nil {
                SignInScreen()
            } else {
                ShellView()
            }
        }
        .preferredColorScheme(model.light ? .light : .dark)
        .tint(Color.jmAccent)
    }
}

/// The first screen on a new phone: which Jarvis this is. The web has no
/// such screen, because the browser's address bar is the answer there. It
/// is drawn as the sign-in screen is, since it is the step before it.
struct ConnectScreen: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            VStack(spacing: JM.Space.s3) {
                VStack(spacing: JM.Space.s2) {
                    Text("Jarvis")
                        .jmText(.display)
                        .foregroundStyle(Color.jmFg)
                        .accessibilityAddTraits(.isHeader)
                    Text("Enter your team’s Jarvis address to continue.")
                        .jmText(.bodySm)
                        .foregroundStyle(Color.jmFgSecondary)
                        .multilineTextAlignment(.center)
                }
                .padding(.bottom, JM.Space.s5)

                JMField("https://", text: $model.originText, large: true) {
                    model.saveOrigin()
                }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .textContentType(.URL)
                .submitLabel(.continue)
                .accessibilityLabel("Jarvis address, starting with https")

                JMButton("Continue", kind: .primary, wide: true, tall: true) {
                    model.saveOrigin()
                }
                .disabled(model.originText.trimmingCharacters(in: .whitespaces).isEmpty)

                if let status = model.status {
                    ErrorLine(status)
                }

                Text("This phone talks to one Jarvis at a time. Nothing is filled in for you.")
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, JM.Space.s4)
            }
            .frame(maxWidth: JM.Size.signIn)
            .padding(.horizontal, JM.Space.s4)
            .padding(.vertical, JM.Space.s12)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .background(Color.jmBg.ignoresSafeArea())
    }
}
