# Adwaita 24H Lockscreen Clock & Weather

A native GNOME Shell extension that replaces the default lock screen clock and date with a customizable Libadwaita-styled interface.

## Key Features
- **24H / 12H Hybrid Analog-Digital Clock:** Sculpted hands with orbiting active digits and circular halo clipping, plus optional seconds hand power-saving mode.
- **Customizable Weather & Metrics Card:** Current temperature, high/low, comparison with yesterday's temperature, 4 toggleable metrics (Humidity, Wind, Feels Like, UV), and 3-day forecast.
- **Multi-Calendar & Weekday Modes:** Gregorian and Hijri/local secondary calendars, single weekday or 7-day horizontal strip, and 22 world numeral systems.
- **Live Interactive Preferences (`prefs.js`):** Real-time sticky preview header across all settings tabs, searchable modal pickers, and built-in country/city database.

## Reviewer Notes (Privacy & Session Mode Justification)
- **`unlock-dialog` Session Mode:** Required because the extension's core functionality is customizing the lock screen clock widget (`Main.screenShield._dialog._clock`). All signals, timers, Soup sessions, and native widgets are cleanly disconnected and restored in `disable()`.
- **Network Requests:** Queries keyless public APIs (`Open-Meteo`, `ipwho.is`, `BigDataCloud`) solely for weather and coarse city localization when automatic location is enabled.
