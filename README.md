# PS5 Game Releases for Omarchy

A Quickshell bar widget for Omarchy that shows upcoming PlayStation 5 releases and the highest-rated PS5 games for a selected calendar month. It gets game data from IGDB and uses exact PS5 release records with conservative platform, date, region, and standalone-game checks.

## Features

- Upcoming PS5 games, ordered by exact Europe/Berlin release date.
- A monthly ranking by IGDB total rating, requiring at least five ratings.
- Previous and next month navigation and manual refresh in both tabs.
- Strict PS5 platform verification, exact release-date precision, regional date handling, and filtering of add-ons and duplicate editions.
- Cover art from IGDB with a bounded local cache and a gamepad placeholder when art is unavailable.
- Clickable game rows that open the exact IGDB game page in the default browser when IGDB supplies a valid URL.
- Six-hour, file-locked data caching. Refreshing the display normally reuses cached data instead of creating a new Twitch token.
- Asynchronous Python requests so the shell UI remains responsive.

## Compatibility

This plugin targets the Omarchy Quattro shell plugin runtime and its Quickshell/Qt Quick imports. It is a `bar-widget` plugin with a panel loaded by `BarWidget.qml`. Use the version of Omarchy that provides the Quattro plugin interface and run the validation commands below on that system.

The plugin runs with your user permissions inside the long-running shell. Review the source before installation; Omarchy plugins are not sandboxed.

## Screenshots

<img width="622" height="820" alt="image" src="https://github.com/user-attachments/assets/4417b5a5-0650-4c13-af06-12b26f5a0ce9" />

<img width="618" height="822" alt="image" src="https://github.com/user-attachments/assets/7df54cdb-8a5e-4803-a9b7-7a40876f5274" />

## Dependencies

- Omarchy Quattro with its Quickshell runtime.
- Python 3.9 or newer (`zoneinfo` is part of the Python standard library).
- Python `requests`.
- `xdg-open` from `xdg-utils` for opening game pages in the default browser.
- A Twitch developer application with IGDB API access.

On Arch Linux or Omarchy, install the Python and browser-launch packages if they are missing:

```sh
sudo pacman -S --needed python python-requests xdg-utils
```

No Python package is installed by the plugin. The application uses only the standard library and `requests`.

## Install

After this repository is public at the URL below, install it with Omarchy:

```sh
omarchy plugin add https://github.com/imanhri/ps5-releases.git --enable
```

Omarchy validates the manifest during installation. You can also check an existing checkout manually:

```sh
omarchy plugin validate .
```

## Configure IGDB access

The plugin uses Twitch application credentials for IGDB. Create a Twitch developer application with a confidential client type, then copy its Client ID and Client Secret. IGDB's setup instructions require a Twitch account with two-factor authentication and explain the application registration steps: [IGDB API account creation and authentication](https://api-docs.igdb.com/#account-creation).

Create the credentials file without putting the values in a shell command or the plugin source:

```sh
install -d -m 700 ~/.config/ps5-releases
${EDITOR:-nano} ~/.config/ps5-releases/credentials
chmod 600 ~/.config/ps5-releases/credentials
```

Enter these two lines in the editor, replacing the placeholders with your own values:

```ini
IGDB_CLIENT_ID=your-client-id
IGDB_CLIENT_SECRET=your-client-secret
```

The file is ignored by Git. Only `games.py` reads it; credentials and access tokens are not placed in the UI JSON, logged, or displayed. If setup is missing or incomplete, either tab shows a short message explaining the file and key names to add.

## Use the tabs

Click the gamepad icon in the top bar to open the panel.

- **Upcoming PS5 Releases** shows eligible games with an exact PS5 release date, days remaining, and a cover when available. The list is sorted by release date.
- **Top Games of the Month** shows eligible PS5 releases in the selected calendar month, ranked by IGDB total rating. Games with fewer than five ratings are not included in the ranking. Use the arrows to change months.
- Use **Refresh** to request the selected data again. The local six-hour cache is kept on disk and protected with a file lock.
- Click a row to open its IGDB game page in your default browser. Rows with no valid IGDB URL remain visible but are not clickable.

## Cover art and cache

Cover metadata comes from IGDB. Images are requested from IGDB's image CDN and cached locally under `~/.cache/ps5-releases/covers/`; the cache is bounded by image count and total size. A missing or failed image uses the bundled gamepad placeholder. Game data is cached for six hours under `$XDG_CACHE_HOME/ps5-releases/`, or `~/.cache/ps5-releases/` when `XDG_CACHE_HOME` is not set. No cover images are bundled in this repository.

## Update

Use Omarchy's plugin updater to review and apply upstream changes:

```sh
omarchy plugin update io.github.imanhri.ps5-releases
```

## Remove

Remove the installed plugin with Omarchy:

```sh
omarchy plugin remove io.github.imanhri.ps5-releases
```

Removing the plugin leaves your IGDB credentials and cached files in place in case you reinstall it. If you also want to delete those, review the paths first and remove only the data you intend to discard. The credentials are in `~/.config/ps5-releases/credentials`; data and covers are in the cache directories described above.

## Troubleshooting

- **Credentials message:** check that the credentials file exists, contains both key names shown above, and is readable by your user. Do not paste secrets into an issue or log.
- **A request fails:** check network access to Twitch and IGDB, then use Refresh. The UI displays a generic API/network error without request details.
- **No games appear:** IGDB may not have a qualifying exact PS5 release date or platform association for that title. The plugin does not substitute a game's general first release date.
- **Covers are blank:** IGDB may not have cover metadata, or the image request may have failed. The row remains usable and shows the placeholder.
- **A game page does not open:** make sure `xdg-open` is available and a default browser is configured. Only safe HTTPS IGDB game-detail URLs are opened.
- **The widget is not visible:** check `omarchy plugin list` and confirm the widget is enabled in your bar layout. Revalidate the plugin and inspect Quickshell's logs for QML errors.
