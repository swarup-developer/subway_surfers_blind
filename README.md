# Subway Surfers Blind Edition

Subway Surfers Blind Edition is an accessibility-first endless runner built for keyboard play, screen readers, and headphone-based spatial audio. It keeps the core lane-switching rhythm of Subway Surfers while adapting the experience for non-visual play with action-focused speech prompts, HRTF-backed threat positioning, and a progression system that remains fully playable without relying on sight.

The project is implemented as a modular Python game on top of `pygame`, with accessibility output through `accessible_output2` and OpenAL Soft HRTF playback through `pyopenalsoft`.

## Highlights

- English-only UI, prompts, HUD, and game flow
- Modular codebase instead of a single monolithic `main.py`
- Keyboard-first gameplay with blind-friendly interaction design
- Threat-focused speech that prioritises the player's current lane
- Speed-adaptive prompt timing and screen-reader rate handling
- Spatial audio pipeline with OpenAL Soft HRTF support
- Persistent progression, inventory, and shop economy
- Unit-tested gameplay, audio, spawning, and progression systems

## Gameplay

The game is a three-lane endless runner. You move left and right between lanes, jump over low obstacles, roll under high obstacles, collect coins and power-ups, and survive as speed ramps up over time.

### Supported core mechanics

- Lane switching
- Jumping
- Rolling
- Hoverboard activation
- Speed progression with difficulty profiles
- Run score, distance, coins, and multiplier tracking
- Pause menu and run recovery flow

### Obstacles

- Trains
- Low barriers
- High barriers
- Bush obstacles

Obstacle spawning is not fully random noise. It is pattern-based and validated so the game avoids impossible lane closures and prefers routes that remain reachable from the player's current lane.

## Accessibility and Audio

This project is built around accessible play rather than visual play with accessibility added later.

### Accessibility features

- Screen-reader announcements for menus, state changes, rewards, and danger prompts
- Current-lane action prompts such as `jump`, `roll`, and `switch`
- Earlier warnings at higher movement speed
- Reduced prompt verbosity at high speed to improve reaction time
- Optional lane announcements on movement
- Blind-friendly menu structure and English labels throughout

### Spatial audio

- OpenAL Soft HRTF playback for in-game sound effects
- Continuous 3D threat tracking for trains and nearby hazards
- Front-to-back train pass movement instead of one-shot warning pings
- Near-miss swish layers for close calls
- Stable footstep placement that does not incorrectly follow lane switching
- Centered player action sounds for jump, roll, dodge, impact, and pickups

For the best experience, play with headphones.

## Power-ups and Inventory

### Run power-ups

- Magnet
- Jetpack
- Double score
- Super Sneakers
- Hoverboard shield

### Run setup and consumables

- Headstart
- Score Booster
- Keys for revive

### Reward and economy systems

- Coins collected during runs are banked after the run ends
- Mystery Boxes during runs
- Shop purchases using banked coins
- Persistent inventory for hoverboards, headstarts, score boosters, and keys

## Progression Systems

The game now includes progression layers inspired by the original game's meta loop.

### Missions

- Multi-goal mission sets
- Mission progress is tracked across runs
- Completing a mission set increases the permanent score multiplier
- At the multiplier cap, mission completion grants a Super Mystery Box instead

### Word Hunt

- Daily word target
- Letter pickups spawn during runs
- Completing the word grants a reward
- Consecutive daily completions increase the streak reward
- High streak rewards can unlock a Super Mystery Box

### Season Hunt

- Seasonal token pickups spawn during runs
- Token thresholds unlock staged rewards
- Reward path includes coins, keys, headstarts, and a Super Mystery Box

### Super Mystery Box

The upgraded reward box can grant larger economy and inventory rewards, including:

- Banked coins
- Hoverboards
- Keys
- Headstarts
- Score Boosters
- Permanent multiplier bonus

## Menus and Flow

The game includes:

- Main Menu
- Run Setup
- Shop
- Options
- How to Play
- Pause Menu
- Revive Menu

## Running the Game

### Requirements

- Windows is the primary target platform
- Python 3.11 or newer recommended
- Headphones recommended

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start the game

```bash
python main.py
```

You can also use:

```bat
run_windows.bat
```

## Controls

- `Left Arrow`: move left
- `Right Arrow`: move right
- `Up Arrow`: jump
- `Down Arrow`: roll
- `Space`: activate hoverboard
- `Escape`: pause / back out of menus
- `Enter`: confirm menu item
- `M`: toggle speech on or off during a run

## Configuration and Save Data

Settings and persistent progression are stored in:

- `data/settings.json`

Saved data includes:

- Audio settings
- Difficulty
- Keys
- Hoverboards
- Headstarts
- Score Boosters
- Banked coins
- Mission progress
- Word Hunt progress
- Season Hunt progress

## Project Structure

```text
main.py
subway_blind/
  app.py
  audio.py
  balance.py
  config.py
  features.py
  game.py
  hrtf_audio.py
  menu.py
  models.py
  progression.py
  spatial_audio.py
  spawn.py
tests/
assets/
data/
```

### Module overview

- `app.py`: application bootstrap
- `game.py`: main gameplay loop, menus, state transitions, rewards, HUD
- `audio.py`: mixer playback, screen-reader wrapper, HRTF fallback handling
- `hrtf_audio.py`: OpenAL Soft 3D source management
- `spatial_audio.py`: threat analysis and spatial warning generation
- `spawn.py`: route-safe obstacle and collectible spawning
- `balance.py`: difficulty and speed-curve tuning
- `features.py`: feature constants and reward tables
- `progression.py`: missions, Word Hunt, Season Hunt, Super Mystery Box logic
- `config.py`: persistent settings and save data
- `models.py`: gameplay data models
- `menu.py`: reusable menu behavior

## Testing

Run the test suite with:

```bash
python -m unittest discover -v
```

The test suite covers:

- Audio channel and pan behavior
- HRTF source handling
- Spatial threat prompting
- Speed balancing
- Spawn safety and route reachability
- Shop and reward logic
- Revive behavior
- Progression systems such as missions, Word Hunt, and Season Hunt

## Notes

- If OpenAL HRTF is unavailable on a machine, the game falls back to its non-HRTF playback path.
- `pygame` may emit a `pkg_resources` deprecation warning depending on the local Python environment. That warning comes from the dependency stack, not from the game logic itself.
- The project is designed to be maintainable and easy to extend. New mechanics should be added to dedicated modules instead of being folded into a single file.
