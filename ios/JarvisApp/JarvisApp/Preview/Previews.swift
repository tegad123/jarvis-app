#if DEBUG
import SwiftUI
import JarvisKit

// One preview per web reference screenshot in ios/reference/web/screens,
// in the same order and under the same name, so the two can be put side by
// side. They run the real screens and the real client against the invented
// rows the screenshots were taken with (PreviewTransport); nothing here
// touches a network.

/// A screen as the app shows it: its model, its background, its theme.
@MainActor
private struct Stage<Screen: View>: View {
    @StateObject private var model: AppModel
    private let screen: Screen

    init(
        _ scenario: PreviewTransport.Scenario = .owner, light: Bool = false, connected: Bool = true,
        @ViewBuilder screen: () -> Screen
    ) {
        Platform.registerFonts()
        let client = connected
            ? PreviewTransport.client(scenario)
            : JarvisClient(transport: PreviewTransport(.signedOut))
        _model = StateObject(
            wrappedValue: AppModel(client: client, session: PreviewTransport.session(scenario), light: light))
        self.screen = screen()
    }

    var body: some View {
        ZStack {
            Color.jmBg.ignoresSafeArea()
            screen
        }
        .environmentObject(model)
        .preferredColorScheme(model.light ? .light : .dark)
        .tint(Color.jmAccent)
    }
}

/// A sheet over an empty screen, as `jmSheet` presents it.
private struct SheetStage<Panel: View>: View {
    @ViewBuilder var panel: () -> Panel

    var body: some View {
        Color.jmBg.ignoresSafeArea().jmSheet(isPresented: true, onClose: {}, panel: panel)
    }
}

private let previewPeople = [
    PersonChoice(personKey: "dana", name: "Dana"),
    PersonChoice(personKey: "omar", name: "Omar"),
    PersonChoice(personKey: "priya", name: "Priya"),
    PersonChoice(personKey: "lena", name: "Lena"),
]

// MARK: - Before sign-in

#Preview("00 Connect (phone only)") {
    Stage(.signedOut, connected: false) { ConnectScreen() }
}

#Preview("01 Sign in") {
    Stage(.signedOut) { SignInScreen() }
}

#Preview("02 Sign in: PIN, people") {
    Stage(.signedOut) { SignInScreen(pinOpen: true) }
}

#Preview("03 Sign in: PIN entry") {
    Stage(.signedOut) { SignInScreen(pinOpen: true, who: "dana") }
}

// MARK: - Home

#Preview("04 Home") {
    Stage { ShellView() }
}

#Preview("05 New channel sheet") {
    Stage {
        SheetStage {
            NewChannelSheet(
                mode: .create, categories: [SelectOption(value: "cat-listings", label: "Listings")],
                busy: false, error: nil, onSubmit: { _, _ in }, onClose: {})
        }
    }
}

#Preview("05b Category sheet") {
    Stage {
        SheetStage {
            CategorySheet(mode: .new, categories: [], busy: false, error: nil, onSave: { _ in }, onDelete: {}, onClose: {})
        }
    }
}

#Preview("20 Home, light") {
    Stage(light: true) { ShellView() }
}

#Preview("23 Home, member") {
    Stage(.member) { ShellView() }
}

// MARK: - Chat

#Preview("06 Ask Jarvis") {
    Stage { ShellView(route: .thread("direct-dana")) }
}

#Preview("06b Ask Jarvis, empty") {
    Stage(.empty) { ShellView(route: .thread("direct-dana")) }
}

#Preview("07 Channel") {
    Stage { ShellView(route: .thread("ch-alder")) }
}

#Preview("08 Channel members sheet") {
    Stage {
        SheetStage {
            MembersSheet(
                channelName: "128 Alder Ave", members: ["dana", "omar"], people: previewPeople, personKey: "dana",
                canManage: true, busy: false, error: nil, onAdd: { _ in }, onRemove: { _ in }, onClose: {})
        }
    }
}

#Preview("09 Channel, empty") {
    Stage { ShellView(route: .thread("ch-marsh")) }
}

#Preview("09b Before you send (phone only)") {
    Stage {
        SheetStage { ConsentSheet(onAllow: {}, onClose: {}) }
    }
}

#Preview("21 Ask Jarvis, light") {
    Stage(light: true) { ShellView(route: .thread("direct-dana")) }
}

// MARK: - Screens

#Preview("10 Activity") {
    Stage { ShellView(route: .nav(.feed)) }
}

#Preview("11 Approvals") {
    Stage { ShellView(route: .nav(.approvals)) }
}

#Preview("12 Inbox") {
    Stage { ShellView(route: .nav(.inbox)) }
}

#Preview("13 Tasks") {
    Stage { ShellView(route: .nav(.tasks)) }
}

#Preview("14 Tasks: new task") {
    Stage { TasksScreen(people: previewPeople, composing: true) }
}

#Preview("15 Leads") {
    Stage { ShellView(route: .nav(.leads)) }
}

#Preview("16 Leads: new lead") {
    Stage { LeadsScreen(people: previewPeople, composing: true) }
}

#Preview("17 Lead sheet") {
    Stage { LeadsScreen(people: previewPeople, open: "ld-1") }
}

#Preview("19 Settings") {
    Stage { ShellView(route: .nav(.settings)) }
}

#Preview("22 Leads, light") {
    Stage(light: true) { ShellView(route: .nav(.leads)) }
}

#Preview("22b Settings, light") {
    Stage(light: true) { ShellView(route: .nav(.settings)) }
}

// MARK: - Empty states

#Preview("Empty: Activity") {
    Stage(.empty) { ShellView(route: .nav(.feed)) }
}

#Preview("Empty: Approvals") {
    Stage(.empty) { ShellView(route: .nav(.approvals)) }
}

#Preview("Empty: Inbox") {
    Stage(.empty) { ShellView(route: .nav(.inbox)) }
}

#Preview("Empty: Tasks") {
    Stage(.empty) { ShellView(route: .nav(.tasks)) }
}

#Preview("Empty: Leads") {
    Stage(.empty) { ShellView(route: .nav(.leads)) }
}

#Preview("Empty: Settings") {
    Stage(.empty) { ShellView(route: .nav(.settings)) }
}

// MARK: - Components

#Preview("Components") {
    Stage {
        ScrollView {
            VStack(alignment: .leading, spacing: JM.Space.s4) {
                FlowLayout {
                    JMButton("Primary", kind: .primary) {}
                    JMButton("Secondary") {}
                    JMButton("Ghost", kind: .ghost) {}
                    JMButton(icon: .plus, label: "Add") {}
                }
                FlowLayout {
                    Tag("Neutral")
                    Tag("Needs you", tone: .action)
                }
                PairingCode(code: "4821-9037", expires: "3:40 PM")
                ErrorState(message: "Jarvis could not read the feed.", detail: "HTTP 503", onRetry: {})
                EmptyState()
                LoadingLine()
            }
            .padding(JM.Space.s4)
        }
    }
}
#endif
