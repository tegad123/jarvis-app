import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

// Jarvis Mono, the web app's design system, as SwiftUI values. Every number
// here is copied from frontend/src/jm/tokens/tokens.css; when a token changes
// there, change it here and nowhere else.

// MARK: - Colour

extension Color {
    /// --jm-bg
    static let jmBg = Color.jm(dark: 0x0A0A0A, light: 0xFFFFFF)
    /// --jm-panel
    static let jmPanel = Color.jm(dark: 0x161616, light: 0xF5F5F5)
    /// --jm-panel-raised
    static let jmPanelRaised = Color.jm(dark: 0x1F1F1F, light: 0xEBEBEB)
    /// --jm-border
    static let jmBorder = Color.jm(dark: 0x262626, light: 0xE5E5E5)
    /// --jm-border-strong
    static let jmBorderStrong = Color.jm(dark: 0x666666, light: 0x8A8A8A)
    /// --jm-fg
    static let jmFg = Color.jm(dark: 0xFFFFFF, light: 0x0A0A0A)
    /// --jm-fg-secondary
    static let jmFgSecondary = Color.jm(dark: 0x8A8A8A, light: 0x666666)
    /// --jm-on-fg
    static let jmOnFg = Color.jm(dark: 0x0A0A0A, light: 0xFFFFFF)
    /// --jm-accent
    static let jmAccent = Color.jm(dark: 0x2563EB, light: 0x2563EB)
    /// --jm-accent-hover: the pressed primary button
    static let jmAccentPressed = Color.jm(dark: 0x1D4ED8, light: 0x1D4ED8)
    /// --jm-on-accent
    static let jmOnAccent = Color.jm(dark: 0xFFFFFF, light: 0xFFFFFF)
    /// --jm-accent-text
    static let jmAccentText = Color.jm(dark: 0x60A5FA, light: 0x2563EB)
    /// --jm-accent-soft
    static let jmAccentSoft = Color.jm(dark: 0x2563EB, light: 0x2563EB, darkAlpha: 0.14, lightAlpha: 0.12)
    /// --jm-accent-soft-text
    static let jmAccentSoftText = Color.jm(dark: 0x60A5FA, light: 0x1D4ED8)
    /// --jm-error
    static let jmError = Color.jm(dark: 0xEF4444, light: 0xEF4444)
    /// --jm-scrim
    static let jmScrim = Color.jm(dark: 0x000000, light: 0x0A0A0A, darkAlpha: 0.6, lightAlpha: 0.32)
    /// The colour of --jm-shadow-float
    static let jmShadow = Color.jm(dark: 0x000000, light: 0x0A0A0A, darkAlpha: 0.6, lightAlpha: 0.12)

    /// The open-house tracker's own temperature swatches (jm/LeadRow).
    static let jmHot = Color(hex: 0xFFC7CE)
    static let jmWarm = Color(hex: 0xFFEB9C)
    static let jmCold = Color(hex: 0xBDD7EE)

    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha)
    }

    /// One colour with a dark and a light value. Dark is the web's default.
    static func jm(dark: UInt32, light: UInt32, darkAlpha: Double = 1, lightAlpha: Double = 1) -> Color {
        #if canImport(UIKit)
        return Color(uiColor: UIColor { traits in
            let isLight = traits.userInterfaceStyle == .light
            let hex = isLight ? light : dark
            return UIColor(
                red: CGFloat((hex >> 16) & 0xFF) / 255,
                green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255,
                alpha: CGFloat(isLight ? lightAlpha : darkAlpha))
        })
        #else
        return Color(hex: dark, alpha: darkAlpha)
        #endif
    }
}

// MARK: - Space, radius, size, motion

enum JM {
    /// --jm-space-*: a 4pt scale. `s8` is 32 and `s12` is 48, as on the web.
    enum Space {
        static let s1: CGFloat = 4
        static let s2: CGFloat = 8
        static let s3: CGFloat = 12
        static let s4: CGFloat = 16
        static let s5: CGFloat = 20
        static let s6: CGFloat = 24
        static let s8: CGFloat = 32
        static let s12: CGFloat = 48
    }

    /// --jm-radius-*
    enum Radius {
        static let tail: CGFloat = 6
        static let sm: CGFloat = 10
        static let md: CGFloat = 14
        static let lg: CGFloat = 16
        static let xl: CGFloat = 18
        static let composer: CGFloat = 26
    }

    /// --jm-size-*
    enum Size {
        static let control: CGFloat = 36
        static let controlSm: CGFloat = 28
        static let touch: CGFloat = 44
        static let composer: CGFloat = 52
        static let thumb: CGFloat = 80
        static let sheet: CGFloat = 480
        static let signIn: CGFloat = 360
        static let page: CGFloat = 760
        static let chat: CGFloat = 720
        /// A bubble is at most this share of the column.
        static let bubble: CGFloat = 0.8
    }

    /// --jm-opacity-disabled
    static let disabledOpacity: Double = 0.35

    /// --jm-duration-*, in seconds
    enum Motion {
        static let press: Double = 0.1
        static let tick: Double = 0.18
        static let sheet: Double = 0.24
        static let dots: Double = 1.2
        static let dotOffset: Double = 0.16
        static let live: Double = 2.0
        /// --jm-ease-out: cubic-bezier(0.2, 0, 0, 1)
        static func easeOut(_ duration: Double) -> Animation {
            .timingCurve(0.2, 0, 0, 1, duration: duration)
        }
    }
}

// MARK: - Type

/// Geist, the one Jarvis Mono typeface, at the three weights the system uses.
enum JMWeight {
    case regular, medium, semibold

    var fontName: String {
        switch self {
        case .regular: return "Geist-Regular"
        case .medium: return "Geist-Medium"
        case .semibold: return "Geist-SemiBold"
        }
    }
}

/// One step of the type scale: size, line height, weight and tracking (in em).
struct JMTextStyle {
    var size: CGFloat
    var line: CGFloat
    var weight: JMWeight
    var tracking: CGFloat = 0

    /// --jm-type-display
    static let display = JMTextStyle(size: 28, line: 34, weight: .semibold, tracking: -0.02)
    /// --jm-type-title
    static let title = JMTextStyle(size: 20, line: 26, weight: .semibold, tracking: -0.01)
    /// --jm-type-heading
    static let heading = JMTextStyle(size: 16, line: 22, weight: .semibold, tracking: -0.005)
    /// --jm-type-body
    static let body = JMTextStyle(size: 15, line: 22, weight: .regular)
    /// --jm-type-body-sm
    static let bodySm = JMTextStyle(size: 14, line: 20, weight: .regular)
    /// --jm-type-label
    static let label = JMTextStyle(size: 14, line: 20, weight: .medium)
    /// --jm-type-caption
    static let caption = JMTextStyle(size: 12, line: 16, weight: .regular)
    /// --jm-type-pairing
    static let pairing = JMTextStyle(size: 28, line: 36, weight: .medium, tracking: 0.04)

    /// Geist's own line is 1.3 em (ascent 1005 + descent 295 over 1000).
    private static let naturalLine: CGFloat = 1.3

    func font(_ override: JMWeight? = nil) -> Font {
        Font.custom((override ?? weight).fontName, size: size)
    }

    /// What has to be added to the font's own line to reach the token's.
    var leading: CGFloat {
        max(0, line - size * JMTextStyle.naturalLine)
    }
}

private struct JMTextModifier: ViewModifier {
    var style: JMTextStyle
    var weight: JMWeight?

    func body(content: Content) -> some View {
        content
            .font(style.font(weight))
            .tracking(style.size * style.tracking)
            .lineSpacing(style.leading)
            .padding(.vertical, style.leading / 2)
    }
}

extension View {
    /// Sets text in a step of the type scale, with the web's line height.
    func jmText(_ style: JMTextStyle, weight: JMWeight? = nil) -> some View {
        modifier(JMTextModifier(style: style, weight: weight))
    }

    /// The font alone, for a field whose height the control already fixes.
    func jmFont(_ style: JMTextStyle, weight: JMWeight? = nil) -> some View {
        font(style.font(weight)).tracking(style.size * style.tracking)
    }

    /// --jm-shadow-float: 0 12px 32px
    func jmFloatShadow() -> some View {
        shadow(color: .jmShadow, radius: 16, x: 0, y: 12)
    }
}
