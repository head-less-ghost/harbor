import SwiftUI

struct HarborAppearance: ViewModifier {
    let appearance: AppAppearance

    func body(content: Content) -> some View {
        content
            .environment(\.harborAppearance, appearance)
            .preferredColorScheme(appearance.colorScheme)
            .toolbarBackgroundVisibility(appearance == .pureDark ? .hidden : .automatic, for: .windowToolbar)
            .containerBackground(
                appearance == .pureDark ? AnyShapeStyle(.black) : AnyShapeStyle(.windowBackground),
                for: .window
            )
    }
}

private struct HarborSurface: ViewModifier {
    @Environment(\.harborAppearance) private var appearance

    func body(content: Content) -> some View {
        content
            .scrollContentBackground(appearance == .pureDark ? .hidden : .automatic)
            .alternatingRowBackgrounds(appearance == .pureDark ? .disabled : .automatic)
            .background(appearance == .pureDark ? Color.black : Color.clear)
    }
}

extension View {
    func harborSurface() -> some View {
        modifier(HarborSurface())
    }
}
