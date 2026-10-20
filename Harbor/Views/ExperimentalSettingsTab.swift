import SafariServices
import SwiftUI

struct ExperimentalSettingsTab: View {
    // TODO: Move Safari setup out of Experimental when the extension is stable.
    @Environment(\.scenePhase) private var scenePhase
    @State private var extensionEnabled: Bool?
    @State private var errorMessage: String?

    private let extensionIdentifier = "co.hapy.harbor.SafariExtension"

    var body: some View {
        Form {
            Section {
                Text("Try features that are still in development.")
                    .foregroundStyle(.secondary)
            }

            Section("Safari Extension") {
                Text("Send download links from Safari to Harbor.")

                LabeledContent("Status") {
                    Text(extensionStatus)
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("settings.experimental.safari.status")
                }

                Button(extensionEnabled == true
                       ? String(localized: "Manage in Safari…")
                       : String(localized: "Enable in Safari…")) {
                    SFSafariApplication.showPreferencesForExtension(withIdentifier: extensionIdentifier) { error in
                        Task { @MainActor in
                            errorMessage = error.map(extensionErrorMessage)
                        }
                    }
                }
                .accessibilityIdentifier("settings.experimental.safari.manage")

                Text("Turn on Harbor in Safari Settings → Extensions, then allow access to the websites you want to use.")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                if let errorMessage {
                    Text(errorMessage)
                        .font(.callout)
                        .foregroundStyle(.red)
                }
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: refreshExtensionState)
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { refreshExtensionState() }
        }
    }

    private var extensionStatus: LocalizedStringResource {
        if let extensionEnabled {
            return extensionEnabled ? "Enabled" : "Disabled"
        }
        return errorMessage == nil ? "Checking…" : "Unavailable"
    }

    private func refreshExtensionState() {
        SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: extensionIdentifier) { state, error in
            extensionEnabled = state?.isEnabled
            errorMessage = error.map(extensionErrorMessage)
        }
    }

    private func extensionErrorMessage(_ error: Error) -> String {
        let error = error as NSError
        if error.domain == SFErrorDomain, error.code == SFErrorCode.noExtensionFound.rawValue {
            return String(localized: "Safari cannot detect the Harbor extension. Reopen Harbor and try again.")
        }
        return String(localized: "Safari could not access the Harbor extension. Try again.")
    }
}
