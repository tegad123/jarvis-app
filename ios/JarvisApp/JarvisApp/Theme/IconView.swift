import SwiftUI
import JarvisKit

/// One icon of the web's set, drawn from the web's own path data.
struct JarvisIconShape: Shape {
    var icon: JarvisIcon

    func path(in rect: CGRect) -> Path {
        let unit = min(rect.width, rect.height) / CGFloat(JarvisIcon.grid)
        let left = rect.minX + (rect.width - unit * CGFloat(JarvisIcon.grid)) / 2
        let top = rect.minY + (rect.height - unit * CGFloat(JarvisIcon.grid)) / 2
        func point(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: left + CGFloat(x) * unit, y: top + CGFloat(y) * unit)
        }
        var path = Path()
        for step in icon.steps {
            switch step {
            case .move(let x, let y):
                path.move(to: point(x, y))
            case .line(let x, let y):
                path.addLine(to: point(x, y))
            case .curve(let x, let y, let c1x, let c1y, let c2x, let c2y):
                path.addCurve(to: point(x, y), control1: point(c1x, c1y), control2: point(c2x, c2y))
            case .close:
                path.closeSubpath()
            }
        }
        return path
    }
}

/// An icon at a size, in the current foreground colour. 1.75 stroke on a
/// 24 grid, round caps and joins, as on the web.
struct Icon: View {
    var icon: JarvisIcon
    var size: CGFloat

    init(_ icon: JarvisIcon, size: CGFloat = 20) {
        self.icon = icon
        self.size = size
    }

    var body: some View {
        Group {
            if icon.isFilled {
                JarvisIconShape(icon: icon).fill()
            } else {
                JarvisIconShape(icon: icon).stroke(style: StrokeStyle(
                    lineWidth: CGFloat(icon.strokeWidth) * size / CGFloat(JarvisIcon.grid),
                    lineCap: .round,
                    lineJoin: .round))
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
