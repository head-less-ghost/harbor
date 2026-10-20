import SwiftUI

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark, pureDark

    var id: String { rawValue }

    var title: LocalizedStringResource {
        switch self {
        case .system: "System"
        case .light: "Light"
        case .dark: "Dark"
        case .pureDark: "Pure Dark"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark, .pureDark: .dark
        }
    }
}

private struct HarborAppearanceKey: EnvironmentKey {
    static let defaultValue = AppAppearance.system
}

extension EnvironmentValues {
    var harborAppearance: AppAppearance {
        get { self[HarborAppearanceKey.self] }
        set { self[HarborAppearanceKey.self] = newValue }
    }
}
