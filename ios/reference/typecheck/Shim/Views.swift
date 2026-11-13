// Containers, controls, and the modifiers the app calls.

// MARK: - Containers

public struct HStack<Content: View>: _Primitive {
    public init(alignment: VerticalAlignment = .center, spacing: CGFloat? = nil, @ViewBuilder content: () -> Content) {}
}

public struct VStack<Content: View>: _Primitive {
    public init(alignment: HorizontalAlignment = .center, spacing: CGFloat? = nil, @ViewBuilder content: () -> Content) {}
}

public struct ZStack<Content: View>: _Primitive {
    public init(alignment: Alignment = .center, @ViewBuilder content: () -> Content) {}
}

public struct LazyVStack<Content: View>: _Primitive {
    public init(alignment: HorizontalAlignment = .center, spacing: CGFloat? = nil, @ViewBuilder content: () -> Content) {}
}

public struct Spacer: _Primitive {
    public init(minLength: CGFloat? = nil) {}
}

public struct Divider: _Primitive {
    public init() {}
}

public struct ScrollView<Content: View>: _Primitive {
    public init(_ axes: Axis.Set = .vertical, showsIndicators: Bool = true, @ViewBuilder content: () -> Content) {}
}

public struct ScrollViewProxy {
    public func scrollTo<ID: Hashable>(_ id: ID, anchor: UnitPoint? = nil) {}
}

public struct ScrollViewReader<Content: View>: _Primitive {
    public init(@ViewBuilder content: @escaping (ScrollViewProxy) -> Content) {}
}

public struct GeometryReader<Content: View>: _Primitive {
    public init(@ViewBuilder content: @escaping (GeometryProxy) -> Content) {}
}

public struct Section<Parent: View, Content: View, Footer: View>: _Primitive {}

extension Section where Parent == Text, Footer == EmptyView {
    public init(_ titleKey: LocalizedStringKey, @ViewBuilder content: () -> Content) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, @ViewBuilder content: () -> Content) {}
}

extension Section where Parent == EmptyView, Footer == EmptyView {
    public init(@ViewBuilder content: () -> Content) {}
}

// MARK: - Controls

public struct Button<Label: View>: _Primitive {
    public init(action: @escaping @MainActor () -> Void, @ViewBuilder label: () -> Label) {}
    public init(role: ButtonRole?, action: @escaping @MainActor () -> Void, @ViewBuilder label: () -> Label) {}
}

extension Button where Label == Text {
    public init(_ titleKey: LocalizedStringKey, action: @escaping @MainActor () -> Void) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, action: @escaping @MainActor () -> Void) {}
    public init(_ titleKey: LocalizedStringKey, role: ButtonRole?, action: @escaping @MainActor () -> Void) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, role: ButtonRole?, action: @escaping @MainActor () -> Void) {}
}

public struct Label<Title: View, Icon: View>: _Primitive {
    public init(@ViewBuilder title: () -> Title, @ViewBuilder icon: () -> Icon) {}
}

public struct Image: Equatable, _Primitive, Sendable {
    public init(_ name: String, bundle: Bundle? = nil) {}
    public init(systemName: String) {}
    public func resizable() -> Image { self }
    public func renderingMode(_ renderingMode: TemplateRenderingMode?) -> Image { self }

    public enum TemplateRenderingMode: Hashable, Sendable {
        case template, original
    }
}

extension Label where Title == Text, Icon == Image {
    public init(_ titleKey: LocalizedStringKey, systemImage name: String) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, systemImage name: String) {}
}

public struct Menu<Label: View, Content: View>: _Primitive {
    public init(@ViewBuilder content: () -> Content, @ViewBuilder label: () -> Label) {}
}

extension Menu where Label == Text {
    public init(_ titleKey: LocalizedStringKey, @ViewBuilder content: () -> Content) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, @ViewBuilder content: () -> Content) {}
}

public struct TextField<Label: View>: _Primitive {}

extension TextField where Label == Text {
    public init(_ titleKey: LocalizedStringKey, text: Binding<String>, prompt: Text? = nil) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, text: Binding<String>, prompt: Text? = nil) {}
    public init(_ titleKey: LocalizedStringKey, text: Binding<String>, axis: Axis) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, text: Binding<String>, axis: Axis) {}
    public init(_ titleKey: LocalizedStringKey, text: Binding<String>, prompt: Text?, axis: Axis) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, text: Binding<String>, prompt: Text?, axis: Axis) {}
}

public struct SecureField<Label: View>: _Primitive {}

extension SecureField where Label == Text {
    public init(_ titleKey: LocalizedStringKey, text: Binding<String>, prompt: Text? = nil) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, text: Binding<String>, prompt: Text? = nil) {}
}

public struct Toggle<Label: View>: _Primitive {
    public init(isOn: Binding<Bool>, @ViewBuilder label: () -> Label) {}
}

extension Toggle where Label == Text {
    public init(_ titleKey: LocalizedStringKey, isOn: Binding<Bool>) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, isOn: Binding<Bool>) {}
}

public struct DatePickerComponents: OptionSet, Sendable {
    public let rawValue: UInt
    public init(rawValue: UInt) { self.rawValue = rawValue }
    public static let hourAndMinute = DatePickerComponents(rawValue: 1)
    public static let date = DatePickerComponents(rawValue: 2)
}

public struct DatePicker<Label: View>: _Primitive {
    public typealias Components = DatePickerComponents
}

extension DatePicker where Label == Text {
    public init(_ titleKey: LocalizedStringKey, selection: Binding<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
    public init(_ titleKey: LocalizedStringKey, selection: Binding<Date>, in range: ClosedRange<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
    public init(_ titleKey: LocalizedStringKey, selection: Binding<Date>, in range: PartialRangeFrom<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, selection: Binding<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, selection: Binding<Date>, in range: ClosedRange<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
    @_disfavoredOverload public init<S: StringProtocol>(_ title: S, selection: Binding<Date>, in range: PartialRangeFrom<Date>, displayedComponents: Components = [.hourAndMinute, .date]) {}
}

@MainActor @preconcurrency
public protocol DatePickerStyle {}

public struct GraphicalDatePickerStyle: DatePickerStyle {
    public init() {}
}

public struct CompactDatePickerStyle: DatePickerStyle {
    public init() {}
}

public struct WheelDatePickerStyle: DatePickerStyle {
    public init() {}
}

extension DatePickerStyle where Self == GraphicalDatePickerStyle {
    public static var graphical: GraphicalDatePickerStyle { GraphicalDatePickerStyle() }
}

extension DatePickerStyle where Self == CompactDatePickerStyle {
    public static var compact: CompactDatePickerStyle { CompactDatePickerStyle() }
}

extension DatePickerStyle where Self == WheelDatePickerStyle {
    public static var wheel: WheelDatePickerStyle { WheelDatePickerStyle() }
}

public struct ProgressView: _Primitive {
    public init() {}
}

// MARK: - Values the modifiers take

public struct AccessibilityTraits: SetAlgebra, Sendable {
    private var bits: UInt64
    public init() { bits = 0 }
    private init(_ bits: UInt64) { self.bits = bits }
    public static let isButton = AccessibilityTraits(1)
    public static let isHeader = AccessibilityTraits(2)
    public static let isSelected = AccessibilityTraits(4)
    public static let isLink = AccessibilityTraits(8)
    public static let isImage = AccessibilityTraits(16)
    public static let isStaticText = AccessibilityTraits(32)
    public static let updatesFrequently = AccessibilityTraits(64)
    public static let isToggle = AccessibilityTraits(128)
    public static let isModal = AccessibilityTraits(256)

    public func contains(_ member: AccessibilityTraits) -> Bool { bits & member.bits == member.bits }
    public func union(_ other: AccessibilityTraits) -> AccessibilityTraits { AccessibilityTraits(bits | other.bits) }
    public func intersection(_ other: AccessibilityTraits) -> AccessibilityTraits { AccessibilityTraits(bits & other.bits) }
    public func symmetricDifference(_ other: AccessibilityTraits) -> AccessibilityTraits { AccessibilityTraits(bits ^ other.bits) }
    @discardableResult public mutating func insert(_ newMember: AccessibilityTraits) -> (inserted: Bool, memberAfterInsert: AccessibilityTraits) {
        bits |= newMember.bits
        return (true, newMember)
    }
    @discardableResult public mutating func remove(_ member: AccessibilityTraits) -> AccessibilityTraits? {
        bits &= ~member.bits
        return member
    }
    @discardableResult public mutating func update(with newMember: AccessibilityTraits) -> AccessibilityTraits? {
        bits |= newMember.bits
        return newMember
    }
    public mutating func formUnion(_ other: AccessibilityTraits) { bits |= other.bits }
    public mutating func formIntersection(_ other: AccessibilityTraits) { bits &= other.bits }
    public mutating func formSymmetricDifference(_ other: AccessibilityTraits) { bits ^= other.bits }
}

public struct AccessibilityChildBehavior: Hashable, Sendable {
    public static let ignore = AccessibilityChildBehavior()
    public static let contain = AccessibilityChildBehavior()
    public static let combine = AccessibilityChildBehavior()
}

public enum UIKeyboardType: Int, Sendable {
    case `default`, asciiCapable, numbersAndPunctuation, URL, numberPad, phonePad, namePhonePad, emailAddress, decimalPad
}

public struct UITextContentType: Hashable, Sendable {
    public static let name = UITextContentType()
    public static let username = UITextContentType()
    public static let password = UITextContentType()
    public static let newPassword = UITextContentType()
    public static let oneTimeCode = UITextContentType()
    public static let emailAddress = UITextContentType()
    public static let telephoneNumber = UITextContentType()
    public static let URL = UITextContentType()
}

public struct TextInputAutocapitalization: Sendable {
    public static var never: TextInputAutocapitalization { TextInputAutocapitalization() }
    public static var words: TextInputAutocapitalization { TextInputAutocapitalization() }
    public static var sentences: TextInputAutocapitalization { TextInputAutocapitalization() }
    public static var characters: TextInputAutocapitalization { TextInputAutocapitalization() }
}

public struct SubmitLabel: Sendable {
    public static var done: SubmitLabel { SubmitLabel() }
    public static var go: SubmitLabel { SubmitLabel() }
    public static var send: SubmitLabel { SubmitLabel() }
    public static var join: SubmitLabel { SubmitLabel() }
    public static var route: SubmitLabel { SubmitLabel() }
    public static var search: SubmitLabel { SubmitLabel() }
    public static var `return`: SubmitLabel { SubmitLabel() }
    public static var next: SubmitLabel { SubmitLabel() }
    public static var `continue`: SubmitLabel { SubmitLabel() }
}

public struct SubmitTriggers: OptionSet, Sendable {
    public let rawValue: UInt
    public init(rawValue: UInt) { self.rawValue = rawValue }
    public static let text = SubmitTriggers(rawValue: 1)
    public static let search = SubmitTriggers(rawValue: 2)
}

public struct ScrollDismissesKeyboardMode: Sendable {
    public static var automatic: ScrollDismissesKeyboardMode { ScrollDismissesKeyboardMode() }
    public static var immediately: ScrollDismissesKeyboardMode { ScrollDismissesKeyboardMode() }
    public static var interactively: ScrollDismissesKeyboardMode { ScrollDismissesKeyboardMode() }
    public static var never: ScrollDismissesKeyboardMode { ScrollDismissesKeyboardMode() }
}

public struct ScrollBounceBehavior: Sendable {
    public static var automatic: ScrollBounceBehavior { ScrollBounceBehavior() }
    public static var always: ScrollBounceBehavior { ScrollBounceBehavior() }
    public static var basedOnSize: ScrollBounceBehavior { ScrollBounceBehavior() }
}

public struct ScrollIndicatorVisibility: Equatable {
    public static var automatic: ScrollIndicatorVisibility { ScrollIndicatorVisibility() }
    public static var visible: ScrollIndicatorVisibility { ScrollIndicatorVisibility() }
    public static var hidden: ScrollIndicatorVisibility { ScrollIndicatorVisibility() }
    public static var never: ScrollIndicatorVisibility { ScrollIndicatorVisibility() }
}

public protocol TextSelectability {}

public struct EnabledTextSelectability: TextSelectability {}
public struct DisabledTextSelectability: TextSelectability {}

extension TextSelectability where Self == EnabledTextSelectability {
    public static var enabled: EnabledTextSelectability { EnabledTextSelectability() }
}

extension TextSelectability where Self == DisabledTextSelectability {
    public static var disabled: DisabledTextSelectability { DisabledTextSelectability() }
}

public struct ContentShapeKinds: OptionSet, Sendable {
    public let rawValue: Int
    public init(rawValue: Int) { self.rawValue = rawValue }
    public static let interaction = ContentShapeKinds(rawValue: 1)
    public static let dragPreview = ContentShapeKinds(rawValue: 2)
    public static let contextMenuPreview = ContentShapeKinds(rawValue: 4)
}

// MARK: - Modifiers

extension View {
    public func modifier<T: ViewModifier>(_ modifier: T) -> some View { self }

    // Layout
    public func frame(width: CGFloat? = nil, height: CGFloat? = nil, alignment: Alignment = .center) -> some View { self }
    public func frame(
        minWidth: CGFloat? = nil, idealWidth: CGFloat? = nil, maxWidth: CGFloat? = nil, minHeight: CGFloat? = nil,
        idealHeight: CGFloat? = nil, maxHeight: CGFloat? = nil, alignment: Alignment = .center
    ) -> some View { self }
    public func fixedSize() -> some View { self }
    public func fixedSize(horizontal: Bool, vertical: Bool) -> some View { self }
    public func padding(_ insets: EdgeInsets) -> some View { self }
    public func padding(_ edges: Edge.Set = .all, _ length: CGFloat? = nil) -> some View { self }
    public func padding(_ length: CGFloat) -> some View { self }
    public func offset(_ offset: CGSize) -> some View { self }
    public func offset(x: CGFloat = 0, y: CGFloat = 0) -> some View { self }
    public func layoutPriority(_ value: Double) -> some View { self }
    public func zIndex(_ value: Double) -> some View { self }
    public func aspectRatio(_ aspectRatio: CGFloat? = nil, contentMode: ContentMode) -> some View { self }
    public func alignmentGuide(_ g: HorizontalAlignment, computeValue: @escaping (ViewDimensions) -> CGFloat) -> some View { self }
    public func alignmentGuide(_ g: VerticalAlignment, computeValue: @escaping (ViewDimensions) -> CGFloat) -> some View { self }
    public func ignoresSafeArea(_ regions: SafeAreaRegions = .all, edges: Edge.Set = .all) -> some View { self }
    public func safeAreaInset<V: View>(edge: VerticalEdge, alignment: HorizontalAlignment = .center, spacing: CGFloat? = nil, @ViewBuilder content: () -> V) -> some View { self }
    public func coordinateSpace<T: Hashable>(name: T) -> some View { self }
    public func coordinateSpace(_ name: NamedCoordinateSpace) -> some View { self }
    public func id<ID: Hashable>(_ id: ID) -> some View { self }

    // Drawing
    public func foregroundColor(_ color: Color?) -> some View { self }
    public func foregroundStyle<S: ShapeStyle>(_ style: S) -> some View { self }
    public func tint<S: ShapeStyle>(_ tint: S?) -> some View { self }
    public func tint(_ tint: Color?) -> some View { self }
    public func opacity(_ opacity: Double) -> some View { self }
    public func hidden() -> some View { self }
    public func clipped(antialiased: Bool = false) -> some View { self }
    public func clipShape<S: Shape>(_ shape: S, style: FillStyle = FillStyle()) -> some View { self }
    public func contentShape<S: Shape>(_ shape: S, eoFill: Bool = false) -> some View { self }
    public func contentShape<S: Shape>(_ kind: ContentShapeKinds, _ shape: S, eoFill: Bool = false) -> some View { self }
    public func shadow(color: Color = Color(.sRGBLinear, white: 0, opacity: 0.33), radius: CGFloat, x: CGFloat = 0, y: CGFloat = 0) -> some View { self }
    public func rotationEffect(_ angle: Angle, anchor: UnitPoint = .center) -> some View { self }
    public func scaleEffect(_ s: CGFloat, anchor: UnitPoint = .center) -> some View { self }
    public func scaleEffect(x: CGFloat = 1.0, y: CGFloat = 1.0, anchor: UnitPoint = .center) -> some View { self }
    public func preferredColorScheme(_ colorScheme: ColorScheme?) -> some View { self }

    public func background<S: ShapeStyle>(_ style: S, ignoresSafeAreaEdges edges: Edge.Set = .all) -> some View { self }
    public func background<S: ShapeStyle, T: Shape>(_ style: S, in shape: T, fillStyle: FillStyle = FillStyle()) -> some View { self }
    public func background<S: ShapeStyle, T: InsettableShape>(_ style: S, in shape: T, fillStyle: FillStyle = FillStyle()) -> some View { self }
    public func background<V: View>(alignment: Alignment = .center, @ViewBuilder content: () -> V) -> some View { self }
    @_disfavoredOverload
    public func background<Background: View>(_ background: Background, alignment: Alignment = .center) -> some View { self }

    public func overlay<S: ShapeStyle>(_ style: S, ignoresSafeAreaEdges edges: Edge.Set = .all) -> some View { self }
    public func overlay<S: ShapeStyle, T: Shape>(_ style: S, in shape: T, fillStyle: FillStyle = FillStyle()) -> some View { self }
    public func overlay<V: View>(alignment: Alignment = .center, @ViewBuilder content: () -> V) -> some View { self }
    @_disfavoredOverload
    public func overlay<Overlay: View>(_ overlay: Overlay, alignment: Alignment = .center) -> some View { self }

    // Text
    public func font(_ font: Font?) -> some View { self }
    public func fontWeight(_ weight: Font.Weight?) -> some View { self }
    public func bold(_ isActive: Bool = true) -> some View { self }
    public func italic(_ isActive: Bool = true) -> some View { self }
    public func monospacedDigit() -> some View { self }
    public func monospaced(_ isActive: Bool = true) -> some View { self }
    public func tracking(_ tracking: CGFloat) -> some View { self }
    public func kerning(_ kerning: CGFloat) -> some View { self }
    public func baselineOffset(_ baselineOffset: CGFloat) -> some View { self }
    public func strikethrough(_ isActive: Bool = true, pattern: Text.LineStyle.Pattern = .solid, color: Color? = nil) -> some View { self }
    public func underline(_ isActive: Bool = true, pattern: Text.LineStyle.Pattern = .solid, color: Color? = nil) -> some View { self }
    public func lineLimit(_ number: Int?) -> some View { self }
    public func lineLimit(_ limit: PartialRangeThrough<Int>) -> some View { self }
    public func lineLimit(_ limit: PartialRangeFrom<Int>) -> some View { self }
    public func lineLimit(_ limit: ClosedRange<Int>) -> some View { self }
    public func lineLimit(_ limit: Int, reservesSpace: Bool) -> some View { self }
    public func lineSpacing(_ lineSpacing: CGFloat) -> some View { self }
    public func multilineTextAlignment(_ alignment: TextAlignment) -> some View { self }
    public func truncationMode(_ mode: Text.TruncationMode) -> some View { self }
    public func minimumScaleFactor(_ factor: CGFloat) -> some View { self }
    public func textCase(_ textCase: Text.Case?) -> some View { self }
    public func textSelection<S: TextSelectability>(_ selectability: S) -> some View { self }

    // Input
    public func disabled(_ disabled: Bool) -> some View { self }
    public func allowsHitTesting(_ enabled: Bool) -> some View { self }
    public func buttonStyle<S: ButtonStyle>(_ style: S) -> some View { self }
    public func datePickerStyle<S: DatePickerStyle>(_ style: S) -> some View { self }
    public func labelsHidden() -> some View { self }
    public func keyboardType(_ type: UIKeyboardType) -> some View { self }
    public func textContentType(_ textContentType: UITextContentType?) -> some View { self }
    public func textInputAutocapitalization(_ autocapitalization: TextInputAutocapitalization?) -> some View { self }
    public func autocorrectionDisabled(_ disable: Bool = true) -> some View { self }
    public func submitLabel(_ submitLabel: SubmitLabel) -> some View { self }
    public func onSubmit(of triggers: SubmitTriggers = .text, _ action: @escaping () -> Void) -> some View { self }
    public func focused(_ condition: FocusState<Bool>.Binding) -> some View { self }
    public func focused<Value: Hashable>(_ binding: FocusState<Value>.Binding, equals value: Value) -> some View { self }
    public func onTapGesture(count: Int = 1, perform action: @escaping () -> Void) -> some View { self }
    public func gesture<T: Gesture>(_ gesture: T, including mask: GestureMask = .all) -> some View { self }
    public func simultaneousGesture<T: Gesture>(_ gesture: T, including mask: GestureMask = .all) -> some View { self }
    public func highPriorityGesture<T: Gesture>(_ gesture: T, including mask: GestureMask = .all) -> some View { self }
    public func contextMenu<MenuItems: View>(@ViewBuilder menuItems: () -> MenuItems) -> some View { self }

    // Scrolling
    public func scrollDismissesKeyboard(_ mode: ScrollDismissesKeyboardMode) -> some View { self }
    public func scrollBounceBehavior(_ behavior: ScrollBounceBehavior, axes: Axis.Set = [.vertical]) -> some View { self }
    public func scrollIndicators(_ visibility: ScrollIndicatorVisibility, axes: Axis.Set = [.vertical, .horizontal]) -> some View { self }
    public func defaultScrollAnchor(_ anchor: UnitPoint?) -> some View { self }

    // Accessibility
    public func accessibilityLabel(_ label: Text) -> some View { self }
    public func accessibilityLabel(_ labelKey: LocalizedStringKey) -> some View { self }
    @_disfavoredOverload public func accessibilityLabel<S: StringProtocol>(_ label: S) -> some View { self }
    public func accessibilityValue(_ value: Text) -> some View { self }
    public func accessibilityValue(_ valueKey: LocalizedStringKey) -> some View { self }
    @_disfavoredOverload public func accessibilityValue<S: StringProtocol>(_ value: S) -> some View { self }
    public func accessibilityHint(_ hint: Text) -> some View { self }
    public func accessibilityHint(_ hintKey: LocalizedStringKey) -> some View { self }
    @_disfavoredOverload public func accessibilityHint<S: StringProtocol>(_ hint: S) -> some View { self }
    public func accessibilityHidden(_ hidden: Bool) -> some View { self }
    public func accessibilityAddTraits(_ traits: AccessibilityTraits) -> some View { self }
    public func accessibilityRemoveTraits(_ traits: AccessibilityTraits) -> some View { self }
    public func accessibilityElement(children: AccessibilityChildBehavior = .ignore) -> some View { self }
    public func accessibilitySortPriority(_ sortPriority: Double) -> some View { self }
    public func accessibilityIdentifier(_ identifier: String) -> some View { self }

    // State and lifecycle
    public func environment<V>(_ keyPath: WritableKeyPath<EnvironmentValues, V>, _ value: V) -> some View { self }
    public func environmentObject<T: ObservableObject>(_ object: T) -> some View { self }
    public func animation<V: Equatable>(_ animation: Animation?, value: V) -> some View { self }
    public func transition(_ t: AnyTransition) -> some View { self }
    public func onAppear(perform action: (() -> Void)? = nil) -> some View { self }
    public func onDisappear(perform action: (() -> Void)? = nil) -> some View { self }
    public func task(priority: TaskPriority = .userInitiated, @_inheritActorContext _ action: @escaping @Sendable () async -> Void) -> some View { self }
    public func task<T: Equatable>(id value: T, priority: TaskPriority = .userInitiated, @_inheritActorContext _ action: @escaping @Sendable () async -> Void) -> some View { self }
    public func onChange<V: Equatable>(of value: V, initial: Bool = false, _ action: @escaping (_ oldValue: V, _ newValue: V) -> Void) -> some View { self }
    public func onChange<V: Equatable>(of value: V, initial: Bool = false, _ action: @escaping () -> Void) -> some View { self }
    @available(*, deprecated, message: "Use the two-parameter or zero-parameter closure.")
    @_disfavoredOverload
    public func onChange<V: Equatable>(of value: V, perform action: @escaping (_ newValue: V) -> Void) -> some View { self }
    public func preference<K: PreferenceKey>(key: K.Type = K.self, value: K.Value) -> some View { self }
    public func onPreferenceChange<K: PreferenceKey>(_ key: K.Type = K.self, perform action: @escaping @Sendable (K.Value) -> Void) -> some View where K.Value: Equatable { self }
}

public enum VerticalEdge: Int8, CaseIterable, Sendable {
    case top, bottom
}
