# Cypress Launcher

The user-facing desktop app for [Cypress](https://github.com/PvZ-Cypress) - dedicated servers for Plants vs. Zombies: Garden Warfare 1, Garden Warfare 2, and Battle for Neighborville.

Built with [Photino.NET](https://www.tryphotino.io/) (HTML/CSS/JS frontend, C# backend) on .NET 8.

## Features

- **Join** servers by IP or through the server browser
- **Host** dedicated servers with full game mode and level selection
- **Server Browser** with master server registration and heartbeats
- **Playlist Editor** for custom level rotations
- **Relay Support** (EU relay toggle) for NAT traversal without port forwarding
- **Moderator Panel** for managing players, kicks, and bans
- **MOTD Editor** with rich text formatting and color gradients
- **Anticheat Toggles** for server-side cheat detection modules
- **Multi-instance** management, run multiple servers/clients from one launcher
- **Side-channel** TCP protocol for remote instance monitoring
- **Smart Pickers** for level, mode, map backgrounds, and character art

## Supported Games

| Game | Version | Notes |
|------|---------|-------|
| Garden Warfare 1 | v1.0.3.0 | |
| Garden Warfare 2 | v1.0.12 | Requires PreEAAC patched executable |
| Battle for Neighborville | Latest | Requires PreEAAC patched executable |

## Prerequisites

- Windows 10+ (Linux is not officially supported, but you may try with Wine/Proton)
- .NET 8.0 Runtime
- A legally owned copy of the game (EA Desktop)
- [Cypress Server DLLs](https://github.com/PvZ-Cypress/Server) (included in releases)

## Building

**Requirements:** Visual Studio 2022+ with the .NET desktop development workload, or just the .NET 8 SDK.

```powershell
# Build via script
.\build.ps1

# Or manually
dotnet publish CypressLauncher.csproj -c Release -f net8.0-windows -o build /p:LangVersion=latest
```

Output goes to `build/`. You'll also need `courgette.exe`, the `.patch` files, and the server DLLs in the same directory for a working release.

## Credits

<table>
  <tr>
    <td align="center"><a href="https://github.com/breakfastbrainz2"><img src="https://github.com/breakfastbrainz2.png" width="60" /><br /><b>BreakfastBrainz2</b></a><br />Original Cypress launcher</td>
    <td align="center"><a href="https://github.com/dotthefox"><img src="https://github.com/dotthefox.png" width="60" /><br /><b>Gargos69Junior</b></a><br />Continuation of the launcher</td>
    <td align="center"><a href="https://github.com/v0ee"><img src="https://github.com/v0ee.png" width="60" /><br /><b>v0e</b></a><br />Launcher revamp</td>
    <td align="center"><a href="https://www.youtube.com/@raymondthejester/"><img src="https://yt3.googleusercontent.com/cHv9bXD3143NfpmJV3KxNhXqymhxcrwtQxzu0d-dWloxXROc06Jp77qaa9wX6fm3AS_XWdjzVQ=s160-c-k-c0x00ffffff-no-rj" width="60" /><br /><b>RaymondTheJester</b></a><br />Logo</td>
  </tr>
</table>

## License

[GPL-3.0](LICENSE)

## Terms of Service
[Terms Of Service](TOS)

