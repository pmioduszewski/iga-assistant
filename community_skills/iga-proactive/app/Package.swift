// swift-tools-version: 5.9
import PackageDescription

// Iga — macOS menu-bar companion for the iga-proactive engine.
//
// HARD CONTRACT (see README + ContractGuard.swift): this package contains
// ZERO job/admission logic. It only renders engine state, relays OS events,
// and shells out to the frozen Python engine scan command. Deleting the
// built .app leaves `/gm` inline fully functional.
//
// The executable target is the menu-bar app (macOS 14+). `build.sh`
// assembles the SwiftPM binary into a proper LSUIElement Iga.app bundle.
//
// Deployment floor raised 13 → 14 for the Observation framework
// (`@Observable`): the stores/controllers migrated off the legacy
// ObservableObject/@Published Combine path. The brief authorizes this raise;
// the build host is macOS 15 and every API used (SMAppService — macOS 13+,
// NSBackgroundActivityScheduler, NSPanel, etc.) remains available at 14.
let package = Package(
    name: "IgaMenuBar",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        .executableTarget(
            name: "IgaMenuBar",
            path: "Sources/IgaMenuBar"
        ),
        .testTarget(
            name: "IgaMenuBarTests",
            dependencies: ["IgaMenuBar"],
            path: "Tests/IgaMenuBarTests",
            resources: [
                .copy("Fixtures")
            ]
        )
    ]
)
