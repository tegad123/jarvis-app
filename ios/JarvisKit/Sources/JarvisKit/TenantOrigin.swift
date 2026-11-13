import Foundation

public enum OriginError: Error, Equatable {
    case empty
    case notHTTPS
    case missingHost
    case credentialsInURL
    case hasQueryOrFragment
}

/// One tenant's HTTPS origin. Nothing here is a customer hostname.
public struct TenantOrigin: Equatable, Sendable {
    public let url: URL
    public let host: String

    public init(url: URL, host: String) {
        self.url = url
        self.host = host
    }

    public static func parse(_ raw: String) -> Result<TenantOrigin, OriginError> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .failure(.empty) }
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased() else {
            return .failure(.notHTTPS)
        }
        guard scheme == "https" else { return .failure(.notHTTPS) }
        if url.user != nil || url.password != nil { return .failure(.credentialsInURL) }
        guard let host = url.host, !host.isEmpty else { return .failure(.missingHost) }
        if url.query != nil || url.fragment != nil { return .failure(.hasQueryOrFragment) }
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        parts?.path = ""
        parts?.query = nil
        parts?.fragment = nil
        guard let clean = parts?.url else { return .failure(.notHTTPS) }
        return .success(TenantOrigin(url: clean, host: host))
    }

    /// `path` is already percent-encoded where it needs to be.
    public func endpoint(_ path: String, query: [URLQueryItem] = []) -> URL {
        var base = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        let suffix = path.hasPrefix("/") ? path : "/" + path
        base.percentEncodedPath = suffix
        if !query.isEmpty { base.queryItems = query }
        return base.url!
    }
}
