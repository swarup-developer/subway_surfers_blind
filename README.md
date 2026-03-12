# Subway Surfers Blind Edition

Accessible endless runner inspired by the lane-based rhythm and reaction loop of Subway Surfers, built for keyboard-first play with speech feedback, spatial audio, HRTF support, and Windows-friendly screen reader integration.

This project is designed as an open source game codebase that can be run from source during development or packaged into a Windows desktop build with PyInstaller.

## Highlights

- Keyboard-only gameplay with spoken lane, hazard, reward, and menu feedback
- Spatial hazard warnings with HRTF-capable OpenAL output through `pyopenalsoft`
- Multiple speech backends: `accessible_output2` by default, optional Microsoft SAPI voices on Windows
- Layered audio system with menu cues, gameplay SFX, ambient warning pulses, and music playback
- Difficulty scaling with readable spawn patterns instead of purely random obstacle spam
- Shop, consumables, revive flow, hoverboards, headstarts, score boosters, and mystery boxes
- Progression systems including missions, Word Hunt, Season Hunt, and Super Mystery Box rewards
- GitHub Releases updater integrated into the in-game menu
- PyInstaller spec for shipping a Windows build with bundled assets and native dependencies

## Gameplay Overview

The core run loop is lane-based:

- Move between left, center, and right lanes
- Jump over low barriers and bushes
- Roll under high barriers
- Avoid trains and react to spoken danger prompts
- Collect coins, keys, power-ups, mystery boxes, Word Hunt letters, and Season Hunt tokens

The game tracks run score, saved coins, consumables, mission metrics, and progression state between sessions.

## Accessibility and Audio

Accessibility is the central design goal of the project.

### Speech and menu support

- Menus are fully navigable with the keyboard
- Menu focus is announced through speech
- Menu open, move, edge, confirm, and close states have dedicated audio cues
- Gameplay can announce lane changes, coin milestones, reward events, and urgent obstacle actions
- Speech can be toggled during gameplay with `M`

### Audio stack

- `pygame` handles the main window, keyboard input, mixing, and music playback
- `pyopenalsoft` powers the HRTF-capable 3D sound path
- `accessible_output2` provides the default assistive speech abstraction
- `pywin32` enables direct SAPI voice selection on Windows

### Spatial danger guidance

The threat audio system tracks the nearest relevant hazard in each lane and produces:

- Directional warning pulses
- Distance-based intensity changes
- Spoken prompts such as `jump`, `roll`, `turn left`, or `turn right`
- Different handling for trains versus closer obstacle types

### Learn Game Sounds

The main menu includes a dedicated sound library browser so players can preview gameplay sounds and understand what each cue means before starting a run.

## Features

### Core systems

- Endless running loop with distance-based speed scaling
- Three difficulty profiles: `easy`, `normal`, `hard`
- Pattern-based obstacle spawning with playability checks
- Near-miss detection and reactive audio feedback
- Pause and revive flows

### Power-ups and run modifiers

- Hoverboard
- Headstart
- Score Booster
- Magnet
- Jetpack
- Double score multiplier
- Super Sneakers
- Pogo

### Economy and rewards

- Persistent coin bank
- Keys
- Hoverboards
- Headstarts
- Score boosters
- Mystery boxes
- Super Mystery Boxes

### Progression

- Mission sets with escalating targets
- Daily Word Hunt
- Monthly Season Hunt progression
- Reward thresholds and unlockable milestone payouts

### Distribution features

- In-game GitHub Releases update check
- Download-and-install update flow for packaged builds
- PyInstaller packaging spec with asset bundling

## Controls

The game now supports keyboard plus SDL-compatible Xbox and PlayStation controllers on both wired and Bluetooth connections. Open `Options -> Controls` to review the active device, see device-specific button labels, and remap keyboard or controller bindings.

### In menus

- `Up` / `W`: move up
- `Down` / `S`: move down
- `Home`: jump to first item
- `End`: jump to last item
- `Enter`: confirm
- `Escape`: close or go back
- `Left` / `Right`: adjust values in the Options menu

### During a run

- `Left Arrow`: move left
- `Right Arrow`: move right
- `Up Arrow`: jump
- `Down Arrow`: roll
- `Space`: activate hoverboard
- `Escape`: pause
- `M`: toggle speech

### Default controller layout

- `D-Pad Up` / `D-Pad Down`: move through menus
- `A` / `Cross`: confirm in menus and jump during a run
- `B` / `Circle`: go back in menus and roll during a run
- `Left Stick Left` / `Left Stick Right`: change lanes during a run
- `X` / `Square`: activate hoverboard
- `Y` / `Triangle`: toggle speech
- `Menu` / `Options`: pause during a run

## Menu Surface

The current user-facing menus include:

- Main Menu
  - Start Game
  - Shop
  - Options
  - How to Play
  - Learn Game Sounds
  - Check for Updates
  - Exit
- Run Setup
  - Headstart selection
  - Score Booster selection
  - Begin Run
- Options
  - SFX volume
  - Music volume
  - Update checks on startup
  - Output device selection
  - Menu HRTF toggle
  - Speech toggle
  - SAPI speech toggle
  - SAPI voice selection
  - SAPI rate
  - SAPI pitch
  - Difficulty
  - Controls
- Controls
  - Active input summary
  - Keyboard bindings
  - Connected controller bindings
- Shop
  - Hoverboards
  - Mystery boxes
  - Headstarts
  - Score boosters

## Requirements

### Runtime dependencies

- Python 3.11 recommended
- `pygame>=2.1`
- `accessible_output2>=0.14`
- `pyopenalsoft>=1.0.0`
- `pywin32>=306` on Windows

The project currently targets Windows first because:

- SAPI voice integration is Windows-specific
- packaged desktop builds are produced with a Windows PyInstaller spec
- save data and updater behavior are tested against the Windows environment

Other platforms may run from source with reduced platform-specific functionality, but Windows is the supported release target.

## Run From Source

### 1. Clone the repository

```bash
git clone https://github.com/oguzhanproductions/subway_surfers_blind.git
cd subway_surfers_blind
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Start the game

```bash
python main.py
```

## Save Data and Local Files

The game stores persistent data in the roaming application data directory on Windows:

```text
%APPDATA%\Vireon Interactive\Subway Surfers Blind Edition\data
```

This includes:

- `settings.json`
- OpenAL configuration
- generated mono cache files for HRTF playback
- updater cache data

The code also contains migration logic to pull forward older save layouts if they exist.

## Project Layout

```text
subway_surfers_blind/
├─ main.py
├─ SubwaySurfersBlind.spec
├─ assets/
│  ├─ menu/
│  ├─ music/
│  └─ sfx/
├─ subway_blind/
│  ├─ app.py
│  ├─ audio.py
│  ├─ balance.py
│  ├─ config.py
│  ├─ features.py
│  ├─ game.py
│  ├─ hrtf_audio.py
│  ├─ menu.py
│  ├─ models.py
│  ├─ progression.py
│  ├─ spatial_audio.py
│  ├─ spawn.py
│  ├─ updater.py
│  └─ version.py
└─ tests/
   └─ test_game.py
```

## Code Architecture

### `main.py`

Minimal entry point. Delegates startup to `subway_blind.app.main()`.

### `subway_blind/app.py`

Initializes `pygame`, loads persisted settings, initializes audio output, creates the main window, and starts the `SubwayBlindGame` loop.

### `subway_blind/game.py`

Primary game controller. Owns:

- menus
- run state
- player state
- obstacle collection
- progression flow
- HUD rendering
- input handling
- reward logic
- updater workflow integration

This is the orchestration layer of the project.

### `subway_blind/audio.py`

Provides:

- mixer initialization and output device selection
- speech abstraction and SAPI integration
- sound effect loading
- menu and gameplay playback
- music track discovery and transitions
- fallback behavior when specific audio backends are unavailable

### `subway_blind/hrtf_audio.py`

Wraps `pyopenalsoft` for 3D sound playback. It also:

- writes an OpenAL Soft configuration file
- enables headphone stereo mode and HRTF
- caches mono-converted `.wav` files when needed
- manages listener and source state

### `subway_blind/spatial_audio.py`

Builds real-time spatial danger cues from obstacle state. This is the layer responsible for directional hazard prompts and pulsing warning feedback.

### `subway_blind/spawn.py`

Controls obstacle and support-item placement using readable route patterns, safe-lane tracking, and playability validation.

### `subway_blind/progression.py`

Handles mission targets, Word Hunt rotation, Season Hunt state, and reward claims.

### `subway_blind/features.py`

Contains balancing rules for consumables, shop prices, reward tables, and mystery box outcomes.

### `subway_blind/balance.py`

Defines per-difficulty speed and spawn-gap curves.

### `subway_blind/config.py`

Manages:

- resource path resolution for source runs and packaged builds
- save data directory resolution
- default settings
- settings load and save
- migration from legacy data locations

### `subway_blind/updater.py`

Implements the GitHub Releases updater:

- latest release lookup
- version comparison
- ZIP download
- extraction
- replacement staging for packaged executables
- restart script generation

### `tests/test_game.py`

Regression coverage for gameplay rules, settings behavior, spawning logic, audio-adjacent flow, and progression behavior.

## Music and Asset Handling

The project resolves static assets through `resource_path()` so the same code works in both cases:

- running directly from source
- running from a PyInstaller-built executable

The main asset groups are:

- `assets/menu`
- `assets/music`
- `assets/sfx`

Music tracks are discovered dynamically from the asset directory, allowing the game to find supported files by base track name.

## Testing

Run the test suite with:

```bash
pytest -q
```

The repository currently includes a large `tests/test_game.py` suite that exercises the game logic heavily without requiring manual play.

## Building a Windows Executable

The repository includes a PyInstaller spec file:

```bash
pyinstaller --clean --noconfirm SubwaySurfersBlind.spec
```

The spec is configured to bundle:

- the Python application
- all project assets
- `pyopenalsoft` native dynamic libraries required for HRTF playback

Build outputs should remain local and should not be committed to source control. The repository ignore rules already exclude `dist/`, `build/`, and local packaging variants.

## Release and Update Flow

Packaged builds can check GitHub Releases and prompt the user to:

- download and install an update
- open the release page
- quit the game

For maintainers, that means the release pipeline should publish a ZIP package that matches the packaged directory structure expected by the updater.

## Development Notes

- The current codebase prefers direct state orchestration over deep framework abstraction
- Windows accessibility support is treated as a first-class requirement
- The audio stack is designed to degrade gracefully when some optional backends are unavailable
- Save settings are persisted automatically when options or progression state change

## Contributing

Issues and pull requests are welcome.

If you contribute:

- keep accessibility behavior intact
- preserve keyboard-only usability
- do not commit `dist/` or `build/` artifacts
- add or update automated tests when gameplay or progression logic changes
- keep audio asset references and packaging behavior consistent with `SubwaySurfersBlind.spec`

## License

This repository is licensed under the terms provided in [LICENSE](LICENSE).
