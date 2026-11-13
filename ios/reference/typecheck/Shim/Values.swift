// Geometry, colour, shape and text values.

// MARK: - Geometry

public enum Axis: Int8, CaseIterable, Sendable {
    case horizontal, vertical

    public struct Set: OptionSet, Sendable {
        public let rawValue: Int8
        public init(rawValue: Int8) { self.rawValue = rawValue }
        public static let horizontal = Set(rawValue: 1)
        public static let vertical = Set(rawValue: 2)
    }
}

public enum Edge: Int8, CaseIterable, Sendable {
    case top, leading, bottom, trailing

    public struct Set: OptionSet, Sendable {
        public let rawValue: Int8
        public init(rawValue: Int8) { self.rawValue = rawValue }
        public static let top = Set(rawValue: 1)
        public static let leading = Set(rawValue: 2)
        public static let bottom = Set(rawValue: 4)
        public static let trailing = Set(rawValue: 8)
        public static let all = Set(rawValue: 15)
        public static let horizontal = Set(rawValue: 10)
        public static let vertical = Set(rawValue: 5)
    }
}

public struct EdgeInsets: Equatable, Sendable {
    public var top: CGFloat
    public var leading: CGFloat
    public var bottom: CGFloat
    public var trailing: CGFloat
    public init(top: CGFloat, leading: CGFloat, bottom: CGFloat, trailing: CGFloat) {
        self.top = top
        self.leading = leading
        self.bottom = bottom
        self.trailing = trailing
    }
    public init() { self.init(top: 0, leading: 0, bottom: 0, trailing: 0) }
}

public struct HorizontalAlignment: Equatable, Sendable {
    let name: String
    public static let leading = HorizontalAlignment(name: "leading")
    public static let center = HorizontalAlignment(name: "center")
    public static let trailing = HorizontalAlignment(name: "trailing")
}

public struct VerticalAlignment: Equatable, Sendable {
    let name: String
    public static let top = VerticalAlignment(name: "top")
    public static let center = VerticalAlignment(name: "center")
    public static let bottom = VerticalAlignment(name: "bottom")
    public static let firstTextBaseline = VerticalAlignment(name: "firstTextBaseline")
    public static let lastTextBaseline = VerticalAlignment(name: "lastTextBaseline")
}

public struct Alignment: Equatable, Sendable {
    public var horizontal: HorizontalAlignment
    public var vertical: VerticalAlignment
    public init(horizontal: HorizontalAlignment, vertical: VerticalAlignment) {
        self.horizontal = horizontal
        self.vertical = vertical
    }
    public static let center = Alignment(horizontal: .center, vertical: .center)
    public static let leading = Alignment(horizontal: .leading, vertical: .center)
    public static let trailing = Alignment(horizontal: .trailing, vertical: .center)
    public static let top = Alignment(horizontal: .center, vertical: .top)
    public static let bottom = Alignment(horizontal: .center, vertical: .bottom)
    public static let topLeading = Alignment(horizontal: .leading, vertical: .top)
    public static let topTrailing = Alignment(horizontal: .trailing, vertical: .top)
    public static let bottomLeading = Alignment(horizontal: .leading, vertical: .bottom)
    public static let bottomTrailing = Alignment(horizontal: .trailing, vertical: .bottom)
}

public struct UnitPoint: Hashable, Sendable {
    public var x: CGFloat
    public var y: CGFloat
    public init(x: CGFloat, y: CGFloat) {
        self.x = x
        self.y = y
    }
    public static let zero = UnitPoint(x: 0, y: 0)
    public static let center = UnitPoint(x: 0.5, y: 0.5)
    public static let leading = UnitPoint(x: 0, y: 0.5)
    public static let trailing = UnitPoint(x: 1, y: 0.5)
    public static let top = UnitPoint(x: 0.5, y: 0)
    public static let bottom = UnitPoint(x: 0.5, y: 1)
    public static let topLeading = UnitPoint(x: 0, y: 0)
    public static let topTrailing = UnitPoint(x: 1, y: 0)
    public static let bottomLeading = UnitPoint(x: 0, y: 1)
    public static let bottomTrailing = UnitPoint(x: 1, y: 1)
}

public struct Angle: Hashable, Sendable {
    public var radians: Double
    public var degrees: Double { radians * 180 / .pi }
    public init(radians: Double) { self.radians = radians }
    public init(degrees: Double) { radians = degrees * .pi / 180 }
    public static let zero = Angle(radians: 0)
    public static func radians(_ radians: Double) -> Angle { Angle(radians: radians) }
    public static func degrees(_ degrees: Double) -> Angle { Angle(degrees: degrees) }
}

public protocol CoordinateSpaceProtocol {}

public struct NamedCoordinateSpace: CoordinateSpaceProtocol, Equatable {}
public struct LocalCoordinateSpace: CoordinateSpaceProtocol {}
public struct GlobalCoordinateSpace: CoordinateSpaceProtocol {}

extension CoordinateSpaceProtocol where Self == NamedCoordinateSpace {
    public static func named(_ name: some Hashable) -> NamedCoordinateSpace { NamedCoordinateSpace() }
}

extension CoordinateSpaceProtocol where Self == LocalCoordinateSpace {
    public static var local: LocalCoordinateSpace { LocalCoordinateSpace() }
}

extension CoordinateSpaceProtocol where Self == GlobalCoordinateSpace {
    public static var global: GlobalCoordinateSpace { GlobalCoordinateSpace() }
}

public enum CoordinateSpace {
    case global
    case local
    case named(AnyHashable)
}

public struct GeometryProxy {
    public var size: CGSize { .zero }
    public var safeAreaInsets: EdgeInsets { EdgeInsets() }
    public func frame(in coordinateSpace: CoordinateSpace) -> CGRect { .zero }
    public func frame(in coordinateSpace: some CoordinateSpaceProtocol) -> CGRect { .zero }
}

public struct SafeAreaRegions: OptionSet, Sendable {
    public let rawValue: UInt
    public init(rawValue: UInt) { self.rawValue = rawValue }
    public static let container = SafeAreaRegions(rawValue: 1)
    public static let keyboard = SafeAreaRegions(rawValue: 2)
    public static let all = SafeAreaRegions(rawValue: 3)
}

public enum ContentMode: Hashable, CaseIterable, Sendable {
    case fit, fill
}

public enum Visibility: Hashable, CaseIterable, Sendable {
    case automatic, visible, hidden
}

public enum CGLineCap: Int32, Sendable {
    case butt, round, square
}

public enum CGLineJoin: Int32, Sendable {
    case miter, round, bevel
}

// MARK: - Shape styles and colour

public protocol ShapeStyle: Sendable {}

public struct Color: Hashable, ShapeStyle, _Primitive, Sendable {
    public enum RGBColorSpace: Hashable, Sendable {
        case sRGB, sRGBLinear, displayP3
    }

    public init(_ colorSpace: RGBColorSpace = .sRGB, red: Double, green: Double, blue: Double, opacity: Double = 1) {}
    public init(_ colorSpace: RGBColorSpace = .sRGB, white: Double, opacity: Double = 1) {}
    public init(hue: Double, saturation: Double, brightness: Double, opacity: Double = 1) {}
    public init(_ name: String, bundle: Bundle? = nil) {}

    public func opacity(_ opacity: Double) -> Color { self }

    public static let clear = Color(white: 0, opacity: 0)
    public static let black = Color(white: 0)
    public static let white = Color(white: 1)
    public static let gray = Color(white: 0.5)
    public static let red = Color(red: 1, green: 0, blue: 0)
    public static let green = Color(red: 0, green: 1, blue: 0)
    public static let blue = Color(red: 0, green: 0, blue: 1)
    public static let orange = Color(red: 1, green: 0.5, blue: 0)
    public static let yellow = Color(red: 1, green: 1, blue: 0)
    public static let primary = Color(white: 0)
    public static let secondary = Color(white: 0.5)
    public static let accentColor = Color(red: 0, green: 0, blue: 1)
}

extension ShapeStyle where Self == Color {
    public static var clear: Color { .clear }
    public static var black: Color { .black }
    public static var white: Color { .white }
    public static var gray: Color { .gray }
    public static var red: Color { .red }
    public static var green: Color { .green }
    public static var blue: Color { .blue }
    public static var orange: Color { .orange }
    public static var yellow: Color { .yellow }
}

public struct HierarchicalShapeStyle: ShapeStyle {
    public static let primary = HierarchicalShapeStyle()
    public static let secondary = HierarchicalShapeStyle()
    public static let tertiary = HierarchicalShapeStyle()
    public static let quaternary = HierarchicalShapeStyle()
}

extension ShapeStyle where Self == HierarchicalShapeStyle {
    public static var primary: HierarchicalShapeStyle { .primary }
    public static var secondary: HierarchicalShapeStyle { .secondary }
    public static var tertiary: HierarchicalShapeStyle { .tertiary }
}

public struct ForegroundStyle: ShapeStyle {
    public init() {}
}

public struct BackgroundStyle: ShapeStyle {
    public init() {}
}

public struct TintShapeStyle: ShapeStyle {
    public init() {}
}

extension ShapeStyle where Self == ForegroundStyle {
    public static var foreground: ForegroundStyle { ForegroundStyle() }
}

extension ShapeStyle where Self == BackgroundStyle {
    public static var background: BackgroundStyle { BackgroundStyle() }
}

extension ShapeStyle where Self == TintShapeStyle {
    public static var tint: TintShapeStyle { TintShapeStyle() }
}

extension ShapeStyle {
    public func opacity(_ opacity: Double) -> some ShapeStyle { self }
}

public struct FillStyle: Equatable, Sendable {
    public var isEOFilled: Bool
    public var isAntialiased: Bool
    public init(eoFill: Bool = false, antialiased: Bool = true) {
        isEOFilled = eoFill
        isAntialiased = antialiased
    }
}

public struct StrokeStyle: Equatable, Sendable {
    public var lineWidth: CGFloat
    public var lineCap: CGLineCap
    public var lineJoin: CGLineJoin
    public var miterLimit: CGFloat
    public var dash: [CGFloat]
    public var dashPhase: CGFloat
    public init(
        lineWidth: CGFloat = 1, lineCap: CGLineCap = .butt, lineJoin: CGLineJoin = .miter, miterLimit: CGFloat = 10,
        dash: [CGFloat] = [], dashPhase: CGFloat = 0
    ) {
        self.lineWidth = lineWidth
        self.lineCap = lineCap
        self.lineJoin = lineJoin
        self.miterLimit = miterLimit
        self.dash = dash
        self.dashPhase = dashPhase
    }
}

// MARK: - Shapes

public struct Path: Equatable, Sendable {
    public init() {}
    public init(_ rect: CGRect) {}
    public init(roundedRect rect: CGRect, cornerRadius: CGFloat, style: RoundedCornerStyle = .continuous) {}
    public init(ellipseIn rect: CGRect) {}
    public init(_ callback: (inout Path) -> Void) {}

    public var isEmpty: Bool { true }
    public var boundingRect: CGRect { .zero }
    public var currentPoint: CGPoint? { nil }

    public mutating func move(to end: CGPoint) {}
    public mutating func addLine(to end: CGPoint) {}
    public mutating func addQuadCurve(to end: CGPoint, control: CGPoint) {}
    public mutating func addCurve(to end: CGPoint, control1: CGPoint, control2: CGPoint) {}
    public mutating func closeSubpath() {}
    public mutating func addRect(_ rect: CGRect) {}
    public mutating func addRoundedRect(in rect: CGRect, cornerSize: CGSize, style: RoundedCornerStyle = .continuous) {}
    public mutating func addEllipse(in rect: CGRect) {}
    public mutating func addLines(_ lines: [CGPoint]) {}
    public mutating func addRelativeArc(center: CGPoint, radius: CGFloat, startAngle: Angle, delta: Angle) {}
    public mutating func addArc(center: CGPoint, radius: CGFloat, startAngle: Angle, endAngle: Angle, clockwise: Bool) {}
    public mutating func addArc(tangent1End: CGPoint, tangent2End: CGPoint, radius: CGFloat) {}
    public mutating func addPath(_ path: Path) {}
    public func offsetBy(dx: CGFloat, dy: CGFloat) -> Path { self }
}

public enum RoundedCornerStyle: Hashable, Sendable {
    case circular, continuous
}

/// The real protocol also drops the main-actor isolation it would inherit
/// from View; here it simply does not inherit from View, and each shape is
/// given its View conformance alongside.
public protocol Shape: Sendable {
    nonisolated func path(in rect: CGRect) -> Path
}

public protocol InsettableShape: Shape {
    associatedtype InsetShape: InsettableShape
    nonisolated func inset(by amount: CGFloat) -> InsetShape
}

public struct _ShapeView: _Primitive {}

public struct _StrokedShape<Base: Shape>: Shape, _Primitive {
    let base: Base
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
}

extension Shape {
    public func fill<S: ShapeStyle>(_ content: S, style: FillStyle = FillStyle()) -> some View { _ShapeView() }
    public func fill(style: FillStyle = FillStyle()) -> some View { _ShapeView() }
    public func stroke<S: ShapeStyle>(_ content: S, style: StrokeStyle, antialiased: Bool = true) -> some View { _ShapeView() }
    public func stroke<S: ShapeStyle>(_ content: S, lineWidth: CGFloat = 1, antialiased: Bool = true) -> some View { _ShapeView() }
    public func stroke(style: StrokeStyle) -> _StrokedShape<Self> { _StrokedShape(base: self) }
    public func stroke(lineWidth: CGFloat = 1) -> _StrokedShape<Self> { _StrokedShape(base: self) }
    public func trim(from startFraction: CGFloat = 0, to endFraction: CGFloat = 1) -> some Shape { self }
    public func rotation(_ angle: Angle, anchor: UnitPoint = .center) -> some Shape { self }
    public func offset(x: CGFloat = 0, y: CGFloat = 0) -> some Shape { self }
}

extension InsettableShape {
    public func strokeBorder<S: ShapeStyle>(_ content: S = .foreground, style: StrokeStyle, antialiased: Bool = true) -> some View { _ShapeView() }
    public func strokeBorder<S: ShapeStyle>(_ content: S = .foreground, lineWidth: CGFloat = 1, antialiased: Bool = true) -> some View { _ShapeView() }
}

public struct Rectangle: InsettableShape, _Primitive {
    public init() {}
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> Rectangle { self }
}

public struct RoundedRectangle: InsettableShape, _Primitive {
    public var cornerSize: CGSize
    public var style: RoundedCornerStyle
    public init(cornerSize: CGSize, style: RoundedCornerStyle = .continuous) {
        self.cornerSize = cornerSize
        self.style = style
    }
    public init(cornerRadius: CGFloat, style: RoundedCornerStyle = .continuous) {
        cornerSize = CGSize(width: cornerRadius, height: cornerRadius)
        self.style = style
    }
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> RoundedRectangle { self }
}

public struct UnevenRoundedRectangle: InsettableShape, _Primitive {
    public init(
        topLeadingRadius: CGFloat = 0, bottomLeadingRadius: CGFloat = 0, bottomTrailingRadius: CGFloat = 0,
        topTrailingRadius: CGFloat = 0, style: RoundedCornerStyle = .continuous
    ) {}
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> UnevenRoundedRectangle { self }
}

public struct Capsule: InsettableShape, _Primitive {
    public var style: RoundedCornerStyle
    public init(style: RoundedCornerStyle = .continuous) { self.style = style }
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> Capsule { self }
}

public struct Circle: InsettableShape, _Primitive {
    public init() {}
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> Circle { self }
}

public struct Ellipse: InsettableShape, _Primitive {
    public init() {}
    public nonisolated func path(in rect: CGRect) -> Path { Path() }
    public nonisolated func inset(by amount: CGFloat) -> Ellipse { self }
}

// MARK: - Fonts and text

public struct Font: Hashable, Sendable {
    public struct Weight: Hashable, Sendable {
        public static let ultraLight = Weight()
        public static let thin = Weight()
        public static let light = Weight()
        public static let regular = Weight()
        public static let medium = Weight()
        public static let semibold = Weight()
        public static let bold = Weight()
        public static let heavy = Weight()
        public static let black = Weight()
    }

    public enum Design: Hashable, Sendable {
        case `default`, serif, rounded, monospaced
    }

    public enum TextStyle: CaseIterable, Hashable, Sendable {
        case largeTitle, title, title2, title3, headline, subheadline, body, callout, footnote, caption, caption2
    }

    public enum Leading: Hashable, Sendable {
        case standard, tight, loose
    }

    public static let largeTitle = Font()
    public static let title = Font()
    public static let title2 = Font()
    public static let title3 = Font()
    public static let headline = Font()
    public static let subheadline = Font()
    public static let body = Font()
    public static let callout = Font()
    public static let footnote = Font()
    public static let caption = Font()
    public static let caption2 = Font()

    public static func system(size: CGFloat, weight: Font.Weight? = nil, design: Font.Design? = nil) -> Font { Font() }
    public static func system(_ style: Font.TextStyle, design: Font.Design? = nil, weight: Font.Weight? = nil) -> Font { Font() }
    public static func custom(_ name: String, size: CGFloat) -> Font { Font() }
    public static func custom(_ name: String, size: CGFloat, relativeTo textStyle: Font.TextStyle) -> Font { Font() }
    public static func custom(_ name: String, fixedSize: CGFloat) -> Font { Font() }

    public func weight(_ weight: Font.Weight) -> Font { self }
    public func italic() -> Font { self }
    public func bold() -> Font { self }
    public func monospacedDigit() -> Font { self }
    public func monospaced() -> Font { self }
    public func leading(_ leading: Font.Leading) -> Font { self }
}

public enum TextAlignment: Hashable, CaseIterable, Sendable {
    case leading, center, trailing
}

/// What may be interpolated into a localized string key.
public protocol _FormatSpecifiable {}
extension Int: _FormatSpecifiable {}
extension Int8: _FormatSpecifiable {}
extension Int16: _FormatSpecifiable {}
extension Int32: _FormatSpecifiable {}
extension Int64: _FormatSpecifiable {}
extension UInt: _FormatSpecifiable {}
extension UInt8: _FormatSpecifiable {}
extension UInt16: _FormatSpecifiable {}
extension UInt32: _FormatSpecifiable {}
extension UInt64: _FormatSpecifiable {}
extension Float: _FormatSpecifiable {}
extension Double: _FormatSpecifiable {}
extension CGFloat: _FormatSpecifiable {}

public struct LocalizedStringKey: Equatable, ExpressibleByStringInterpolation {
    public struct StringInterpolation: StringInterpolationProtocol {
        public init(literalCapacity: Int, interpolationCount: Int) {}
        public mutating func appendLiteral(_ literal: String) {}
        public mutating func appendInterpolation(_ string: String) {}
        public mutating func appendInterpolation<T: _FormatSpecifiable>(_ value: T) {}
        public mutating func appendInterpolation<T: _FormatSpecifiable>(_ value: T, specifier: String) {}
        public mutating func appendInterpolation(_ text: Text) {}
        public mutating func appendInterpolation(_ attributedString: AttributedString) {}
        public mutating func appendInterpolation(_ date: Date, style: Text.DateStyle) {}
    }

    public init(_ value: String) {}
    public init(stringLiteral value: String) {}
    public init(stringInterpolation: StringInterpolation) {}
}

public struct Text: Equatable, _Primitive, Sendable {
    public struct LineStyle: Hashable, Sendable {
        public struct Pattern: Hashable, Sendable {
            public static let solid = Pattern()
            public static let dot = Pattern()
            public static let dash = Pattern()
        }
        public init(pattern: Pattern = .solid, color: Color? = nil) {}
        public static let single = LineStyle()
    }

    public struct DateStyle: Hashable, Sendable {
        public static let time = DateStyle()
        public static let date = DateStyle()
        public static let relative = DateStyle()
        public static let offset = DateStyle()
        public static let timer = DateStyle()
    }

    public enum TruncationMode: Hashable, Sendable {
        case head, tail, middle
    }

    public enum Case: Hashable, Sendable {
        case uppercase, lowercase
    }

    public init(_ key: LocalizedStringKey, tableName: String? = nil, bundle: Bundle? = nil, comment: StaticString? = nil) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ content: S) {}
    public init(verbatim content: String) {}
    public init(_ attributedContent: AttributedString) {}
    public init(_ date: Date, style: Text.DateStyle) {}

    public static func + (lhs: Text, rhs: Text) -> Text { lhs }

    public func foregroundColor(_ color: Color?) -> Text { self }
    public func foregroundStyle<S: ShapeStyle>(_ style: S) -> Text { self }
    public func font(_ font: Font?) -> Text { self }
    public func fontWeight(_ weight: Font.Weight?) -> Text { self }
    public func bold() -> Text { self }
    public func italic() -> Text { self }
    public func monospacedDigit() -> Text { self }
    public func monospaced() -> Text { self }
    public func strikethrough(_ isActive: Bool = true, color: Color? = nil) -> Text { self }
    public func strikethrough(_ isActive: Bool = true, pattern: Text.LineStyle.Pattern, color: Color? = nil) -> Text { self }
    public func underline(_ isActive: Bool = true, color: Color? = nil) -> Text { self }
    public func underline(_ isActive: Bool = true, pattern: Text.LineStyle.Pattern, color: Color? = nil) -> Text { self }
    public func kerning(_ kerning: CGFloat) -> Text { self }
    public func tracking(_ tracking: CGFloat) -> Text { self }
    public func baselineOffset(_ baselineOffset: CGFloat) -> Text { self }
}

// The attributes SwiftUI reads from an AttributedString.

extension AttributeScopes {
    public struct SwiftUIAttributes: AttributeScope {
        public enum FontAttribute: AttributedStringKey {
            public typealias Value = Font
            public static let name = "SwiftUI.Font"
        }
        public enum ForegroundColorAttribute: AttributedStringKey {
            public typealias Value = Color
            public static let name = "SwiftUI.ForegroundColor"
        }
        public enum BackgroundColorAttribute: AttributedStringKey {
            public typealias Value = Color
            public static let name = "SwiftUI.BackgroundColor"
        }
        public enum UnderlineStyleAttribute: AttributedStringKey {
            public typealias Value = Text.LineStyle
            public static let name = "SwiftUI.UnderlineStyle"
        }
        public enum StrikethroughStyleAttribute: AttributedStringKey {
            public typealias Value = Text.LineStyle
            public static let name = "SwiftUI.StrikethroughStyle"
        }
        public enum KernAttribute: AttributedStringKey {
            public typealias Value = CGFloat
            public static let name = "SwiftUI.Kern"
        }
        public enum TrackingAttribute: AttributedStringKey {
            public typealias Value = CGFloat
            public static let name = "SwiftUI.Tracking"
        }

        public let font: FontAttribute
        public let foregroundColor: ForegroundColorAttribute
        public let backgroundColor: BackgroundColorAttribute
        public let underlineStyle: UnderlineStyleAttribute
        public let strikethroughStyle: StrikethroughStyleAttribute
        public let kern: KernAttribute
        public let tracking: TrackingAttribute
    }

    public var swiftUI: SwiftUIAttributes.Type { SwiftUIAttributes.self }
}

extension AttributeDynamicLookup {
    public subscript<T: AttributedStringKey>(dynamicMember keyPath: KeyPath<AttributeScopes.SwiftUIAttributes, T>) -> T {
        self[T.self]
    }
}
