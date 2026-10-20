import SwiftUI

struct TorrentVPNSettingsContent: View {
    @Bindable var controller: TorrentVPNController

    var body: some View {
        LabeledContent("Profile", value: controller.profileName ?? String(localized: "No profile selected"))
        Button("Import VPN Profile…", action: controller.importProfile)
            .accessibilityIdentifier("settings.vpn.import")
        Text("Use an OpenVPN .ovpn profile with inline certificates, or a WireGuard .conf profile. Profiles and credentials are stored in Keychain.")
            .font(.caption)
            .foregroundStyle(.secondary)
        if controller.profileName?.lowercased().hasSuffix(".ovpn") == true {
            TextField("VPN username", text: $controller.username)
                .accessibilityIdentifier("settings.vpn.username")
            SecureField("VPN password", text: $controller.password)
                .accessibilityIdentifier("settings.vpn.password")
        }
        HStack {
            if controller.isConnecting { ProgressView().controlSize(.small) }
            Text(controller.message)
                .font(.callout)
                .accessibilityIdentifier("settings.vpn.status")
            Spacer()
            if case .connected = controller.route {
                Button("Disconnect", action: controller.disconnect)
            } else if controller.isConnecting {
                Button("Cancel", action: controller.disconnect)
            } else {
                Button("Connect", action: controller.connect)
                    .disabled(controller.profileName == nil)
                    .accessibilityIdentifier("settings.vpn.connect")
            }
        }
        // TODO: Add provider-specific port forwarding after profile-based VPNs are stable.
    }
}
