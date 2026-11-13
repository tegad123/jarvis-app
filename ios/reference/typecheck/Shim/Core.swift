// A stand-in for SwiftUI's declarations, for type-checking the app's
// sources on Linux where there is no Xcode. It draws nothing and is never
// part of an iOS build. Signatures follow the real framework's as closely
// as a hand-written copy can; it covers what the app uses and no more.
// A clean check here means names, labels and types line up against this
// copy. It is not a substitute for building in Xcode.

@_exported import Foundation

// MARK: - View

@MainActor @preconcurrency
public protocol View {
    associatedtype Body: View
    @ViewBuilder @MainActor @preconcurrency var body: Body { get }
}

extension Never: View {
    public var body: Never { fatalError() }
}

/// A view the framework draws itself.
public protocol _Primitive: View where Body == Never {}

extension _Primitive {
    public var body: Never { fatalError() }
}

public struct EmptyView: _Primitive {
    public init() {}
}

public struct AnyView: _Primitive {
    public init<V: View>(_ view: V) {}
}

public struct TupleView<T>: _Primitive {
    public var value: T
    public init(_ value: T) { self.value = value }
}

public struct _ConditionalContent<TrueContent: View, FalseContent: View>: _Primitive {}

extension Optional: View where Wrapped: View {
    public typealias Body = Never
    public var body: Never { fatalError() }
}

@resultBuilder
public struct ViewBuilder {
    public static func buildExpression<Content: View>(_ content: Content) -> Content { content }
    public static func buildBlock() -> EmptyView { EmptyView() }
    public static func buildBlock<Content: View>(_ content: Content) -> Content { content }
    public static func buildBlock<each Content: View>(_ content: repeat each Content) -> TupleView<(repeat each Content)> {
        TupleView((repeat each content))
    }
    public static func buildIf<Content: View>(_ content: Content?) -> Content? { content }
    public static func buildEither<T: View, F: View>(first: T) -> _ConditionalContent<T, F> { _ConditionalContent() }
    public static func buildEither<T: View, F: View>(second: F) -> _ConditionalContent<T, F> { _ConditionalContent() }
    public static func buildLimitedAvailability<Content: View>(_ content: Content) -> AnyView { AnyView(content) }
}

public struct Group<Content: View>: _Primitive {
    public init(@ViewBuilder content: () -> Content) {}
}

public struct ForEach<Data: RandomAccessCollection, ID: Hashable, Content: View>: _Primitive {
    public init(_ data: Data, id: KeyPath<Data.Element, ID>, @ViewBuilder content: @escaping (Data.Element) -> Content) {}
}

extension ForEach where ID == Data.Element.ID, Data.Element: Identifiable {
    public init(_ data: Data, @ViewBuilder content: @escaping (Data.Element) -> Content) {}
}

extension ForEach where Data == Range<Int>, ID == Int {
    public init(_ data: Range<Int>, @ViewBuilder content: @escaping (Int) -> Content) {}
}

// MARK: - Modifiers, styles

@MainActor @preconcurrency
public protocol ViewModifier {
    associatedtype Body: View
    typealias Content = _ViewModifier_Content<Self>
    @ViewBuilder @MainActor @preconcurrency func body(content: Content) -> Body
}

public struct _ViewModifier_Content<Modifier: ViewModifier>: _Primitive {}

public struct ButtonRole: Equatable, Sendable {
    public static let destructive = ButtonRole()
    public static let cancel = ButtonRole()
}

public struct ButtonStyleConfiguration {
    public struct Label: _Primitive {}
    public let role: ButtonRole?
    public let label: Label
    public let isPressed: Bool
}

@MainActor @preconcurrency
public protocol ButtonStyle {
    associatedtype Body: View
    typealias Configuration = ButtonStyleConfiguration
    @ViewBuilder @MainActor @preconcurrency func makeBody(configuration: Configuration) -> Body
}

public struct PlainButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View { configuration.label }
}

public struct BorderlessButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View { configuration.label }
}

extension ButtonStyle where Self == PlainButtonStyle {
    public static var plain: PlainButtonStyle { PlainButtonStyle() }
}

extension ButtonStyle where Self == BorderlessButtonStyle {
    public static var borderless: BorderlessButtonStyle { BorderlessButtonStyle() }
}

// MARK: - State

@propertyWrapper
public struct State<Value> {
    private final class Box { var value: Value; init(_ value: Value) { self.value = value } }
    private let box: Box

    public init(wrappedValue: Value) { box = Box(wrappedValue) }
    public init(initialValue: Value) { box = Box(initialValue) }

    public var wrappedValue: Value {
        get { box.value }
        nonmutating set { box.value = newValue }
    }

    public var projectedValue: Binding<Value> {
        let box = self.box
        return Binding(get: { box.value }, set: { box.value = $0 })
    }
}

extension State where Value: ExpressibleByNilLiteral {
    public init() { self.init(wrappedValue: nil) }
}

@propertyWrapper @dynamicMemberLookup
public struct Binding<Value> {
    private let getter: () -> Value
    private let setter: (Value) -> Void

    public init(get: @escaping () -> Value, set: @escaping (Value) -> Void) {
        getter = get
        setter = set
    }

    public init(projectedValue: Binding<Value>) { self = projectedValue }

    public static func constant(_ value: Value) -> Binding<Value> {
        Binding(get: { value }, set: { _ in })
    }

    public var wrappedValue: Value {
        get { getter() }
        nonmutating set { setter(newValue) }
    }

    public var projectedValue: Binding<Value> { self }

    public subscript<Subject>(dynamicMember keyPath: WritableKeyPath<Value, Subject>) -> Binding<Subject> {
        Binding<Subject>(
            get: { self.wrappedValue[keyPath: keyPath] },
            set: { self.wrappedValue[keyPath: keyPath] = $0 })
    }
}

extension Binding {
    public init<V>(_ base: Binding<V>) where Value == V? {
        self.init(get: { base.wrappedValue }, set: { if let value = $0 { base.wrappedValue = value } })
    }
}

public final class ObservableObjectPublisher {
    public init() {}
    public func send() {}
}

public protocol ObservableObject: AnyObject {
    associatedtype ObjectWillChangePublisher = ObservableObjectPublisher
    var objectWillChange: ObjectWillChangePublisher { get }
}

extension ObservableObject where ObjectWillChangePublisher == ObservableObjectPublisher {
    public var objectWillChange: ObservableObjectPublisher { ObservableObjectPublisher() }
}

@propertyWrapper
public struct Published<Value> {
    public var wrappedValue: Value
    public init(wrappedValue: Value) { self.wrappedValue = wrappedValue }
    public init(initialValue: Value) { self.wrappedValue = initialValue }
}

@dynamicMemberLookup
public struct _ObjectBindings<ObjectType: ObservableObject> {
    let object: ObjectType
    public subscript<Subject>(dynamicMember keyPath: ReferenceWritableKeyPath<ObjectType, Subject>) -> Binding<Subject> {
        let object = self.object
        return Binding(get: { object[keyPath: keyPath] }, set: { object[keyPath: keyPath] = $0 })
    }
}

@MainActor @preconcurrency @propertyWrapper
public struct StateObject<ObjectType: ObservableObject> {
    private let make: () -> ObjectType
    public init(wrappedValue thunk: @autoclosure @escaping () -> ObjectType) { make = thunk }
    public var wrappedValue: ObjectType { make() }
    public var projectedValue: _ObjectBindings<ObjectType> { _ObjectBindings(object: make()) }
}

@MainActor @preconcurrency @propertyWrapper
public struct ObservedObject<ObjectType: ObservableObject> {
    public init(wrappedValue: ObjectType) { self.wrappedValue = wrappedValue }
    public var wrappedValue: ObjectType
    public var projectedValue: _ObjectBindings<ObjectType> { _ObjectBindings(object: wrappedValue) }
}

@MainActor @preconcurrency @propertyWrapper
public struct EnvironmentObject<ObjectType: ObservableObject> {
    public init() {}
    public var wrappedValue: ObjectType { fatalError() }
    public var projectedValue: _ObjectBindings<ObjectType> { fatalError() }
}

@propertyWrapper
public struct FocusState<Value: Hashable> {
    @propertyWrapper
    public struct Binding {
        public var wrappedValue: Value {
            get { fatalError() }
            nonmutating set {}
        }
        public var projectedValue: Binding { self }
        public init() {}
    }

    public init() where Value == Bool {}
    public init<T: Hashable>() where Value == T? {}

    public var wrappedValue: Value {
        get { fatalError() }
        nonmutating set {}
    }

    public var projectedValue: Binding { Binding() }
}

// MARK: - Environment

public protocol EnvironmentKey {
    associatedtype Value
    static var defaultValue: Value { get }
}

public enum ColorScheme: Hashable, Sendable, CaseIterable {
    case light, dark
}

public enum ScenePhase: Comparable, Hashable, Sendable {
    case background, inactive, active
}

public enum LayoutDirection: Hashable, Sendable {
    case leftToRight, rightToLeft
}

public struct OpenURLAction {
    public func callAsFunction(_ url: URL) {}
}

public struct DismissAction {
    public func callAsFunction() {}
}

public struct EnvironmentValues {
    public init() {}

    public subscript<K: EnvironmentKey>(key: K.Type) -> K.Value {
        get { K.defaultValue }
        set {}
    }

    public var colorScheme: ColorScheme {
        get { .dark }
        set {}
    }
    public var scenePhase: ScenePhase {
        get { .active }
        set {}
    }
    public var layoutDirection: LayoutDirection {
        get { .leftToRight }
        set {}
    }
    public var isEnabled: Bool {
        get { true }
        set {}
    }
    public var accessibilityReduceMotion: Bool { false }
    public var displayScale: CGFloat {
        get { 2 }
        set {}
    }
    public var openURL: OpenURLAction { OpenURLAction() }
    public var dismiss: DismissAction { DismissAction() }
}

@propertyWrapper
public struct Environment<Value> {
    public init(_ keyPath: KeyPath<EnvironmentValues, Value>) {}
    public var wrappedValue: Value { fatalError() }
}

public protocol PreferenceKey {
    associatedtype Value
    static var defaultValue: Value { get }
    static func reduce(value: inout Value, nextValue: () -> Value)
}

// MARK: - App

@MainActor @preconcurrency
public protocol Scene {
    associatedtype Body: Scene
    @SceneBuilder @MainActor @preconcurrency var body: Body { get }
}

extension Never: Scene {}

@resultBuilder
public struct SceneBuilder {
    public static func buildBlock<Content: Scene>(_ content: Content) -> Content { content }
}

public struct WindowGroup<Content: View>: Scene {
    public init(@ViewBuilder content: @escaping () -> Content) {}
    public var body: Never { fatalError() }
}

@MainActor @preconcurrency
public protocol App {
    associatedtype Body: Scene
    @SceneBuilder @MainActor @preconcurrency var body: Body { get }
    @MainActor @preconcurrency init()
}

extension App {
    @MainActor @preconcurrency public static func main() {}
}

// MARK: - Animation

public struct Animation: Equatable, Sendable {
    public static let `default` = Animation()
    public static var easeIn: Animation { Animation() }
    public static var easeOut: Animation { Animation() }
    public static var easeInOut: Animation { Animation() }
    public static var linear: Animation { Animation() }
    public static var spring: Animation { Animation() }
    public static func easeIn(duration: TimeInterval) -> Animation { Animation() }
    public static func easeOut(duration: TimeInterval) -> Animation { Animation() }
    public static func easeInOut(duration: TimeInterval) -> Animation { Animation() }
    public static func linear(duration: TimeInterval) -> Animation { Animation() }
    public static func timingCurve(_ p1x: Double, _ p1y: Double, _ p2x: Double, _ p2y: Double, duration: TimeInterval = 0.35) -> Animation { Animation() }
    public static func spring(response: Double = 0.5, dampingFraction: Double = 0.825, blendDuration: TimeInterval = 0) -> Animation { Animation() }
    public static func spring(duration: TimeInterval = 0.5, bounce: Double = 0.0, blendDuration: Double = 0) -> Animation { Animation() }
    public func delay(_ delay: TimeInterval) -> Animation { self }
    public func speed(_ speed: Double) -> Animation { self }
    public func repeatCount(_ repeatCount: Int, autoreverses: Bool = true) -> Animation { self }
    public func repeatForever(autoreverses: Bool = true) -> Animation { self }
}

public func withAnimation<Result>(_ animation: Animation? = .default, _ body: () throws -> Result) rethrows -> Result {
    try body()
}

public struct AnyTransition {
    public static let identity = AnyTransition()
    public static let opacity = AnyTransition()
    public static var scale: AnyTransition { AnyTransition() }
    public static var slide: AnyTransition { AnyTransition() }
    public static func scale(scale: CGFloat, anchor: UnitPoint = .center) -> AnyTransition { AnyTransition() }
    public static func move(edge: Edge) -> AnyTransition { AnyTransition() }
    public static func offset(x: CGFloat = 0, y: CGFloat = 0) -> AnyTransition { AnyTransition() }
    public static func asymmetric(insertion: AnyTransition, removal: AnyTransition) -> AnyTransition { AnyTransition() }
    public func combined(with other: AnyTransition) -> AnyTransition { self }
    public func animation(_ animation: Animation?) -> AnyTransition { self }
}

// MARK: - Gestures

@MainActor @preconcurrency
public protocol Gesture<Value> {
    associatedtype Value
}

public struct GestureMask: OptionSet, Sendable {
    public let rawValue: UInt32
    public init(rawValue: UInt32) { self.rawValue = rawValue }
    public static let none = GestureMask([])
    public static let gesture = GestureMask(rawValue: 1)
    public static let subviews = GestureMask(rawValue: 2)
    public static let all = GestureMask(rawValue: 3)
}

public struct _ChangedGesture<Base: Gesture>: Gesture {
    public typealias Value = Base.Value
}

public struct _EndedGesture<Base: Gesture>: Gesture {
    public typealias Value = Base.Value
}

extension Gesture {
    public func onEnded(_ action: @escaping (Value) -> Void) -> _EndedGesture<Self> { _EndedGesture() }
}

extension Gesture where Value: Equatable {
    public func onChanged(_ action: @escaping (Value) -> Void) -> _ChangedGesture<Self> { _ChangedGesture() }
}

public struct DragGesture: Gesture {
    public struct Value: Equatable, Sendable {
        public var time: Date
        public var location: CGPoint
        public var startLocation: CGPoint
        public var translation: CGSize
        public var velocity: CGSize
        public var predictedEndLocation: CGPoint
        public var predictedEndTranslation: CGSize
    }

    public init(minimumDistance: CGFloat = 10, coordinateSpace: CoordinateSpace = .local) {}
    public init(minimumDistance: CGFloat = 10, coordinateSpace: some CoordinateSpaceProtocol = .local) {}
}

public struct TapGesture: Gesture {
    public typealias Value = Void
    public init(count: Int = 1) {}
}

// MARK: - Layout protocol

public protocol Layout {
    associatedtype Cache = Void
    typealias Subviews = LayoutSubviews
    func makeCache(subviews: Subviews) -> Cache
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Cache) -> CGSize
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Cache)
}

extension Layout where Cache == Void {
    public func makeCache(subviews: Subviews) {}
}

extension Layout {
    public func callAsFunction<V: View>(@ViewBuilder _ content: () -> V) -> some View { EmptyView() }
}

public struct ProposedViewSize: Equatable, Sendable {
    public var width: CGFloat?
    public var height: CGFloat?
    public static let zero = ProposedViewSize(width: 0, height: 0)
    public static let unspecified = ProposedViewSize(width: nil, height: nil)
    public static let infinity = ProposedViewSize(width: .infinity, height: .infinity)
    public init(width: CGFloat?, height: CGFloat?) {
        self.width = width
        self.height = height
    }
    public init(_ size: CGSize) {
        width = size.width
        height = size.height
    }
    public func replacingUnspecifiedDimensions(by size: CGSize = CGSize(width: 10, height: 10)) -> CGSize {
        CGSize(width: width ?? size.width, height: height ?? size.height)
    }
}

public struct ViewDimensions: Equatable {
    public var width: CGFloat { 0 }
    public var height: CGFloat { 0 }
    public subscript(guide: HorizontalAlignment) -> CGFloat { 0 }
    public subscript(guide: VerticalAlignment) -> CGFloat { 0 }
}

public struct ViewSpacing: Sendable {
    public init() {}
    public func distance(to next: ViewSpacing, along axis: Axis) -> CGFloat { 0 }
}

public struct LayoutSubview: Equatable {
    public var priority: Double { 0 }
    public var spacing: ViewSpacing { ViewSpacing() }
    public func sizeThatFits(_ proposal: ProposedViewSize) -> CGSize { .zero }
    public func dimensions(in proposal: ProposedViewSize) -> ViewDimensions { ViewDimensions() }
    public func place(at position: CGPoint, anchor: UnitPoint = .topLeading, proposal: ProposedViewSize) {}
}

public struct LayoutSubviews: Equatable, RandomAccessCollection, Sendable {
    public typealias Element = LayoutSubview
    public typealias Index = Int
    public typealias SubSequence = LayoutSubviews
    public var startIndex: Int { 0 }
    public var endIndex: Int { 0 }
    public subscript(index: Int) -> LayoutSubview { fatalError() }
    public subscript(bounds: Range<Int>) -> LayoutSubviews { self }
}

// MARK: - Previews

/// What check.sh turns each `#Preview("name") { … }` into.
@MainActor
public func _Preview<Content: View>(_ name: String? = nil, @ViewBuilder body: @MainActor () -> Content) {}
