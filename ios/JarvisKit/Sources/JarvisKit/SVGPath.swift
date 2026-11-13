import Foundation

/// One drawing step of an icon, in the icon's own 24-unit grid.
public enum PathStep: Equatable, Sendable {
    case move(x: Double, y: Double)
    case line(x: Double, y: Double)
    case curve(x: Double, y: Double, c1x: Double, c1y: Double, c2x: Double, c2y: Double)
    case close
}

/// Reads an SVG path's `d` attribute, so the phone draws the web's icons
/// from the web's own path data instead of look-alike symbols.
public enum SVGPath {
    public static func steps(_ d: String) -> [PathStep] {
        var reader = Reader(text: Array(d.unicodeScalars))
        var steps: [PathStep] = []
        var x = 0.0, y = 0.0
        var startX = 0.0, startY = 0.0
        var lastC2: (x: Double, y: Double)?
        var command: Unicode.Scalar = "M"

        while true {
            reader.skipSeparators()
            guard let next = reader.peek else { break }
            if next.properties.isAlphabetic {
                command = next
                reader.advance()
            } else if command == "Z" || command == "z" {
                break
            } else if command == "M" {
                command = "L"
            } else if command == "m" {
                command = "l"
            }
            let relative = command.properties.isLowercase
            let dx = relative ? x : 0
            let dy = relative ? y : 0
            var keptC2: (x: Double, y: Double)?

            switch command {
            case "M", "m":
                guard let px = reader.number(), let py = reader.number() else { return steps }
                x = px + dx
                y = py + dy
                startX = x
                startY = y
                steps.append(.move(x: x, y: y))
            case "L", "l":
                guard let px = reader.number(), let py = reader.number() else { return steps }
                x = px + dx
                y = py + dy
                steps.append(.line(x: x, y: y))
            case "H", "h":
                guard let px = reader.number() else { return steps }
                x = px + dx
                steps.append(.line(x: x, y: y))
            case "V", "v":
                guard let py = reader.number() else { return steps }
                y = py + dy
                steps.append(.line(x: x, y: y))
            case "C", "c":
                guard let c1x = reader.number(), let c1y = reader.number(),
                      let c2x = reader.number(), let c2y = reader.number(),
                      let px = reader.number(), let py = reader.number() else { return steps }
                steps.append(.curve(x: px + dx, y: py + dy, c1x: c1x + dx, c1y: c1y + dy, c2x: c2x + dx, c2y: c2y + dy))
                keptC2 = (c2x + dx, c2y + dy)
                x = px + dx
                y = py + dy
            case "S", "s":
                guard let c2x = reader.number(), let c2y = reader.number(),
                      let px = reader.number(), let py = reader.number() else { return steps }
                let c1x = lastC2.map { 2 * x - $0.x } ?? x
                let c1y = lastC2.map { 2 * y - $0.y } ?? y
                steps.append(.curve(x: px + dx, y: py + dy, c1x: c1x, c1y: c1y, c2x: c2x + dx, c2y: c2y + dy))
                keptC2 = (c2x + dx, c2y + dy)
                x = px + dx
                y = py + dy
            case "A", "a":
                guard let rx = reader.number(), let ry = reader.number(), let rotation = reader.number(),
                      let large = reader.flag(), let sweep = reader.flag(),
                      let px = reader.number(), let py = reader.number() else { return steps }
                steps.append(contentsOf: arc(
                    fromX: x, fromY: y, rx: rx, ry: ry, rotation: rotation,
                    largeArc: large, sweep: sweep, toX: px + dx, toY: py + dy))
                x = px + dx
                y = py + dy
            case "Z", "z":
                steps.append(.close)
                x = startX
                y = startY
            default:
                return steps
            }
            lastC2 = keptC2
        }
        return steps
    }

    /// An elliptical arc as cubic curves (SVG implementation notes, F.6.5),
    /// in pieces of a quarter turn or less.
    static func arc(
        fromX x1: Double, fromY y1: Double, rx rawRx: Double, ry rawRy: Double, rotation: Double,
        largeArc: Bool, sweep: Bool, toX x2: Double, toY y2: Double
    ) -> [PathStep] {
        var rx = abs(rawRx)
        var ry = abs(rawRy)
        if x1 == x2 && y1 == y2 { return [] }
        if rx == 0 || ry == 0 { return [.line(x: x2, y: y2)] }

        let phi = rotation * .pi / 180
        let cosPhi = cos(phi)
        let sinPhi = sin(phi)
        let halfX = (x1 - x2) / 2
        let halfY = (y1 - y2) / 2
        let px = cosPhi * halfX + sinPhi * halfY
        let py = -sinPhi * halfX + cosPhi * halfY

        let lambda = (px * px) / (rx * rx) + (py * py) / (ry * ry)
        if lambda > 1 {
            rx *= lambda.squareRoot()
            ry *= lambda.squareRoot()
        }

        let numerator = rx * rx * ry * ry - rx * rx * py * py - ry * ry * px * px
        let denominator = rx * rx * py * py + ry * ry * px * px
        var factor = denominator == 0 ? 0 : max(0, numerator / denominator).squareRoot()
        if largeArc == sweep { factor = -factor }
        let cxPrime = factor * rx * py / ry
        let cyPrime = -factor * ry * px / rx
        let cx = cosPhi * cxPrime - sinPhi * cyPrime + (x1 + x2) / 2
        let cy = sinPhi * cxPrime + cosPhi * cyPrime + (y1 + y2) / 2

        func angle(_ ux: Double, _ uy: Double, _ vx: Double, _ vy: Double) -> Double {
            atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        }
        let startAngle = angle(1, 0, (px - cxPrime) / rx, (py - cyPrime) / ry)
        var delta = angle((px - cxPrime) / rx, (py - cyPrime) / ry, (-px - cxPrime) / rx, (-py - cyPrime) / ry)
        if !sweep && delta > 0 { delta -= 2 * .pi }
        if sweep && delta < 0 { delta += 2 * .pi }

        let pieces = max(1, Int((abs(delta) / (.pi / 2) - 1e-9).rounded(.up)))
        let step = delta / Double(pieces)
        let handle = 4.0 / 3.0 * tan(step / 4)

        func point(_ theta: Double) -> (x: Double, y: Double) {
            let ex = rx * cos(theta)
            let ey = ry * sin(theta)
            return (cx + cosPhi * ex - sinPhi * ey, cy + sinPhi * ex + cosPhi * ey)
        }
        func tangent(_ theta: Double) -> (x: Double, y: Double) {
            let ex = -rx * sin(theta)
            let ey = ry * cos(theta)
            return (cosPhi * ex - sinPhi * ey, sinPhi * ex + cosPhi * ey)
        }

        var out: [PathStep] = []
        for index in 0..<pieces {
            let from = startAngle + Double(index) * step
            let to = from + step
            let p0 = point(from)
            let p1 = point(to)
            let t0 = tangent(from)
            let t1 = tangent(to)
            let end = index == pieces - 1 ? (x: x2, y: y2) : p1
            out.append(.curve(
                x: end.x, y: end.y,
                c1x: p0.x + handle * t0.x, c1y: p0.y + handle * t0.y,
                c2x: p1.x - handle * t1.x, c2y: p1.y - handle * t1.y))
        }
        return out
    }

    private struct Reader {
        let text: [Unicode.Scalar]
        var at = 0

        var peek: Unicode.Scalar? { at < text.count ? text[at] : nil }

        mutating func advance() { at += 1 }

        mutating func skipSeparators() {
            while let c = peek, c == " " || c == "," || c == "\n" || c == "\t" || c == "\r" { at += 1 }
        }

        /// Arc flags are single digits and may run into the next number.
        mutating func flag() -> Bool? {
            skipSeparators()
            guard let c = peek, c == "0" || c == "1" else { return nil }
            at += 1
            return c == "1"
        }

        /// SVG packs numbers: "-.5.5" is -0.5 then 0.5.
        mutating func number() -> Double? {
            skipSeparators()
            var out = ""
            if let c = peek, c == "-" || c == "+" {
                out.unicodeScalars.append(c)
                at += 1
            }
            var seenDot = false
            var seenDigit = false
            while let c = peek {
                if c.value >= 0x30 && c.value <= 0x39 {
                    seenDigit = true
                } else if c == "." && !seenDot {
                    seenDot = true
                } else {
                    break
                }
                out.unicodeScalars.append(c)
                at += 1
            }
            if seenDigit, let c = peek, c == "e" || c == "E" {
                var exponent = "e"
                var ahead = at + 1
                if ahead < text.count, text[ahead] == "-" || text[ahead] == "+" {
                    exponent.unicodeScalars.append(text[ahead])
                    ahead += 1
                }
                var digits = false
                while ahead < text.count, text[ahead].value >= 0x30, text[ahead].value <= 0x39 {
                    exponent.unicodeScalars.append(text[ahead])
                    ahead += 1
                    digits = true
                }
                if digits {
                    out += exponent
                    at = ahead
                }
            }
            guard seenDigit else { return nil }
            return Double(out)
        }
    }
}
