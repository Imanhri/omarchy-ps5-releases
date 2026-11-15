import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.imanhri.ps5-releases"
  ipcTarget: "io.github.imanhri.ps5-releases"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var games: []
  property bool loading: false
  property bool hasLoaded: false
  property bool failed: false
  property bool credentialsMissing: false
  property bool cached: false
  property string updatedAt: ""
  property date today: new Date()
  property int activeTab: 0
  property int selectedYear: new Date().getFullYear()
  property int selectedMonth: new Date().getMonth() + 1
  property var monthlyGames: []
  property bool monthlyLoading: false
  property bool monthlyHasLoaded: false
  property bool monthlyFailed: false
  property bool monthlyCredentialsMissing: false
  property bool monthlyCached: false
  property int monthlyUnrankedCount: 0
  property string monthlyUpdatedAt: ""
  property string monthlyLoadedKey: ""
  property string monthlyRequestKey: ""
  property bool detailsVisible: false
  property var selectedGame: null
  property var detailData: null
  property bool detailLoading: false
  property bool detailFailed: false
  property bool detailCredentialsMissing: false
  property int detailProcessGameId: 0
  property real listScrollPosition: 0

  readonly property var barIdentity: hostWidget || root
  readonly property color contentForeground: bar ? bar.foreground : Commons.Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string scriptPath: localFilePath(Qt.resolvedUrl("games.py"))

  Commons.Translations {
    id: strings
    directory: Qt.resolvedUrl("i18n")
  }

  function localFilePath(url) {
    var value = decodeURIComponent(String(url))
    return value.indexOf("file://") === 0 ? value.substring(7) : value
  }

  function open() {
    root.controller.show()
    // Reuse this panel's data while it's fresh. Python checks its own
    // six-hour disk cache after a shell reload or an explicit refresh.
    if (root.activeTab === 0) {
      if (!root.hasFreshData()) root.refresh()
    } else if (!root.hasFreshMonthlyData()) {
      root.refreshMonthly(false)
    }
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function closeForPopoutSwitch() {
    root.popoutSwitchClosing = true
    root.close()
    Qt.callLater(function() { root.popoutSwitchClosing = false })
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function refresh() {
    if (fetchProcess.running) return
    root.loading = true
    root.failed = false
    root.credentialsMissing = false
    fetchProcess.command = ["python3", root.scriptPath]
    fetchProcess.running = true
  }

  function selectedMonthKey() {
    return String(root.selectedYear).padStart(4, "0") + "-" + String(root.selectedMonth).padStart(2, "0")
  }

  function hasFreshMonthlyData() {
    if (!root.monthlyHasLoaded || root.monthlyFailed
        || root.monthlyLoadedKey !== root.selectedMonthKey() || root.monthlyUpdatedAt === "") return false
    var fetchedAt = Date.parse(root.monthlyUpdatedAt)
    var age = Date.now() - fetchedAt
    return isFinite(age) && age >= 0 && age < 6 * 60 * 60 * 1000
  }

  function selectTab(index) {
    root.activeTab = index
    if (index === 1 && !root.hasFreshMonthlyData()) root.refreshMonthly(false)
  }

  function moveMonth(delta) {
    if (root.monthlyLoading) return
    var next = new Date(root.selectedYear, root.selectedMonth - 1 + delta, 1, 12)
    root.selectedYear = next.getFullYear()
    root.selectedMonth = next.getMonth() + 1
    root.monthlyGames = []
    root.monthlyLoadedKey = ""
    root.monthlyHasLoaded = false
    root.monthlyFailed = false
    root.monthlyUpdatedAt = ""
    root.monthlyUnrankedCount = 0
    root.refreshMonthly(false)
  }

  function refreshMonthly(force) {
    if (monthlyProcess.running) return
    root.monthlyRequestKey = root.selectedMonthKey()
    root.monthlyLoading = true
    root.monthlyFailed = false
    root.monthlyCredentialsMissing = false
    monthlyProcess.command = ["python3", root.scriptPath, "--top-month", root.monthlyRequestKey]
    if (force === true) monthlyProcess.command.push("--force")
    monthlyProcess.running = true
  }

  function monthLabel() {
    var firstOfMonth = new Date(root.selectedYear, root.selectedMonth - 1, 1, 12)
    return firstOfMonth.toLocaleDateString(Qt.locale(Commons.I18n.formatLocale), "MMMM yyyy")
  }

  function handleMonthlyOutput(raw, requestKey) {
    root.monthlyLoading = false
    var payload
    try {
      payload = JSON.parse(String(raw || "").trim())
    } catch (error) {
      root.monthlyFailed = true
      root.monthlyCredentialsMissing = false
      return
    }

    var unranked = Number(payload && payload.unranked_count)
    if (!payload || payload.ok !== true || payload.month !== requestKey
        || !Array.isArray(payload.games) || payload.games.length > 10
        || !Number.isInteger(unranked) || unranked < 0 || unranked > 5000) {
      root.monthlyFailed = true
      root.monthlyCredentialsMissing = payload && payload.error === "credentials_missing"
      return
    }

    var seen = ({})
    var safeGames = []
    for (var i = 0; i < payload.games.length; i++) {
      var game = payload.games[i]
      if (!game || typeof game.name !== "string" || typeof game.released !== "string") continue
      var id = Number(game.id)
      var rating = Number(game.rating)
      var ratingCount = Number(game.rating_count)
      var name = game.name.substring(0, 160)
      var releasedDate = new Date(game.released + "T12:00:00Z")
      if (!Number.isInteger(id) || id <= 0 || seen[String(id)]
          || !name || isNaN(releasedDate.getTime())
          || !isFinite(rating) || rating < 0 || rating > 100
          || !Number.isInteger(ratingCount) || ratingCount < 5
          || game.released.substring(0, 7) !== requestKey) continue
      seen[String(id)] = true
      safeGames.push({
        id: id,
        name: name,
        rating: rating,
        rating_count: ratingCount,
        released: game.released,
        cover_url: root.safeCoverUrl(game.cover_url),
        cover_cache_url: root.safeCachedCoverUrl(game.cover_cache_url, game.cover_url),
        game_url: root.safeGameUrl(game.game_url)
      })
    }

    root.monthlyGames = safeGames
    root.monthlyUnrankedCount = unranked
    root.monthlyUpdatedAt = typeof payload.updated_at === "string" ? payload.updated_at : ""
    root.monthlyCached = payload.cached === true
    root.monthlyLoadedKey = requestKey
    root.monthlyHasLoaded = true
    root.monthlyFailed = false
    root.monthlyCredentialsMissing = false
  }

  function monthlyRatingLabel(value) {
    var rating = Number(value)
    if (!isFinite(rating) || rating < 0 || rating > 100)
      return strings.t("monthly.unrated")
    return strings.t("monthly.ratingOutOf100").arg(Commons.I18n.formatNumber(rating, 1))
  }

  function monthlyUpdatedLabel() {
    var parsed = new Date(root.monthlyUpdatedAt)
    var timestamp = root.monthlyUpdatedAt && !isNaN(parsed.getTime())
      ? parsed.toLocaleString(Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat)
      : ""
    if (timestamp === "") return ""
    return strings.t(root.monthlyCached ? "monthly.cachedAt" : "monthly.fetchedAt").arg(timestamp)
  }

  function hasFreshData() {
    if (!root.hasLoaded || root.failed || root.updatedAt === "") return false
    var fetchedAt = Date.parse(root.updatedAt)
    var age = Date.now() - fetchedAt
    return isFinite(age) && age >= 0 && age < 6 * 60 * 60 * 1000
  }

  function handleOutput(raw) {
    root.loading = false
    var payload
    try {
      payload = JSON.parse(String(raw || "").trim())
    } catch (error) {
      root.failed = true
      root.credentialsMissing = false
      return
    }

    if (!payload || payload.ok !== true || !Array.isArray(payload.games) || payload.games.length > 10) {
      root.failed = true
      root.credentialsMissing = payload && payload.error === "credentials_missing"
      return
    }

    var safeGames = []
    for (var i = 0; i < payload.games.length; i++) {
      var game = payload.games[i]
      if (!game || typeof game.name !== "string" || typeof game.released !== "string") continue
      var name = game.name.substring(0, 160)
      var days = Number(game.days_left)
      var id = Number(game.id)
      if (!name || !/^\d{4}-\d{2}-\d{2}$/.test(game.released)
          || !Number.isInteger(days) || days < 0 || days > 180
          || !Number.isInteger(id) || id <= 0) continue
      safeGames.push({
        id: id,
        name: name,
        released: game.released,
        days_left: days,
        cover_url: root.safeCoverUrl(game.cover_url),
        cover_cache_url: root.safeCachedCoverUrl(game.cover_cache_url, game.cover_url),
        game_url: root.safeGameUrl(game.game_url)
      })
    }

    root.games = safeGames
    root.updatedAt = typeof payload.updated_at === "string" ? payload.updated_at : ""
    root.cached = payload.cached === true
    root.hasLoaded = true
    root.failed = false
    root.credentialsMissing = false
  }

  function formattedDate(value) {
    var parsed = new Date(String(value) + "T12:00:00Z")
    if (isNaN(parsed.getTime())) return String(value).substring(0, 10)
    return parsed.toLocaleDateString(Qt.locale(Commons.I18n.formatLocale), strings.t("panel.dateFormat"))
  }

  function safeCoverUrl(value) {
    var url = String(value || "")
    return /^https:\/\/images\.igdb\.com\/igdb\/image\/upload\/t_cover_small\/[A-Za-z0-9_-]{1,128}\.jpg$/.test(url)
      ? url : ""
  }

  function safeCachedCoverUrl(value, coverUrl) {
    var imageUrl = root.safeCoverUrl(coverUrl)
    var imageMatch = imageUrl.match(/\/([A-Za-z0-9_-]{1,128})\.jpg$/)
    if (!imageMatch) return ""
    var cached = String(value || "")
    var expectedSuffix = "/ps5-releases/covers/" + imageMatch[1] + ".jpg"
    return cached.indexOf("file:///") === 0
      && cached.slice(-expectedSuffix.length) === expectedSuffix
      && cached.indexOf("?") < 0 && cached.indexOf("#") < 0 ? cached : ""
  }

  function safeGameUrl(value) {
    var url = String(value || "")
    return url.length <= 2048 && !/[\u0000-\u0020\u007f]/.test(url)
      && /^https:\/\/(?:www\.)?igdb\.com\/games\/[a-z0-9][a-z0-9-]{0,199}\/?$/i.test(url)
      ? url : ""
  }

  function openGame(value) {
    var safeUrl = root.safeGameUrl(value)
    if (!safeUrl || gameOpenProcess.running) return
    gameOpenProcess.command = ["xdg-open", safeUrl]
    gameOpenProcess.running = true
  }

  function showDetails(game) {
    var id = Number(game && game.id)
    if (!Number.isInteger(id) || id <= 0 || id > 2147483647) return
    var content = popup.loadedContent
    if (!content) return
    root.listScrollPosition = content.listScroll.contentY
    root.selectedGame = game
    root.detailData = null
    root.detailLoading = true
    root.detailFailed = false
    root.detailCredentialsMissing = false
    root.detailsVisible = true
    content.detailScroll.contentY = 0
    root.startDetailFetch()
  }

  function backToList() {
    root.detailsVisible = false
    root.selectedGame = null
    root.detailData = null
    root.detailLoading = false
    Qt.callLater(function() {
      var content = popup.loadedContent
      if (content) content.listScroll.contentY = root.listScrollPosition
    })
  }

  function startDetailFetch() {
    if (!root.selectedGame || detailProcess.running) return
    root.detailProcessGameId = root.selectedGame.id
    detailProcess.command = ["python3", root.scriptPath, "--details", String(root.detailProcessGameId)]
    detailProcess.running = true
  }

  function handleDetailOutput(raw) {
    if (!root.selectedGame || root.detailProcessGameId !== root.selectedGame.id) return
    var payload
    try {
      payload = JSON.parse(String(raw || "").trim())
    } catch (error) {
      root.detailFailed = true
      root.detailLoading = false
      return
    }
    if (payload && payload.game_id !== undefined
        && Number(payload.game_id) !== root.selectedGame.id) return
    if (payload && payload.ok === true && payload.game
        && Number(payload.game.id) !== root.selectedGame.id) return
    if (!payload || payload.ok !== true || !payload.game
        || Number(payload.game.id) !== root.selectedGame.id
        || typeof payload.game.name !== "string" || !payload.game.name) {
      root.detailFailed = true
      root.detailCredentialsMissing = payload && payload.error === "credentials_missing"
      root.detailLoading = false
      return
    }
    var game = payload.game
    root.detailData = {
      name: game.name.substring(0, 512),
      summary: typeof game.summary === "string" ? game.summary.substring(0, 8000) : "",
      storyline: typeof game.storyline === "string" ? game.storyline.substring(0, 8000) : "",
      genres: root.safeDetailNames(game.genres),
      developers: root.safeDetailNames(game.developers),
      publishers: root.safeDetailNames(game.publishers),
      platforms: root.safeDetailNames(game.platforms),
      rating: typeof game.rating === "number" && isFinite(game.rating)
        && game.rating >= 0 && game.rating <= 100 ? game.rating : null,
      cover_url: root.safeCoverUrl(game.cover_url),
      cover_cache_url: root.safeCachedCoverUrl(game.cover_cache_url, game.cover_url),
      artwork_url: root.safeArtworkUrl(game.artwork_url),
      game_url: root.safeGameUrl(game.game_url)
    }
    root.detailFailed = false
    root.detailLoading = false
  }

  function safeDetailNames(value) {
    if (!Array.isArray(value)) return []
    return value.slice(0, 20).filter(function(name) {
      return typeof name === "string" && name.trim().length > 0
    }).map(function(name) { return name.substring(0, 160) })
  }

  function safeArtworkUrl(value) {
    var url = String(value || "")
    return /^https:\/\/images\.igdb\.com\/igdb\/image\/upload\/t_screenshot_med\/[A-Za-z0-9_-]{1,128}\.jpg$/.test(url)
      ? url : ""
  }

  function detailHeroSource(fallback) {
    var game = root.detailData
    if (!fallback && game && game.artwork_url) return game.artwork_url
    var cover = game && game.cover_url ? game.cover_url : root.selectedGame && root.selectedGame.cover_url
    var safeCover = root.safeCoverUrl(cover)
    if (!fallback && safeCover) return safeCover.replace("/t_cover_small/", "/t_cover_big/")
    var local = root.safeCachedCoverUrl(root.selectedGame && root.selectedGame.cover_cache_url,
                                        root.selectedGame && root.selectedGame.cover_url)
    return local || safeCover
  }

  function detailMetaRows() {
    if (!root.selectedGame) return []
    var game = root.detailData || ({})
    var rows = [{ label: strings.t("details.releaseDate"), value: root.formattedDate(root.selectedGame.released) }]
    if (game.genres && game.genres.length) rows.push({ label: strings.t("details.genres"), value: game.genres.join(", ") })
    if (game.developers && game.developers.length) rows.push({ label: strings.t("details.developers"), value: game.developers.join(", ") })
    if (game.publishers && game.publishers.length) rows.push({ label: strings.t("details.publishers"), value: game.publishers.join(", ") })
    if (game.platforms && game.platforms.length) rows.push({ label: strings.t("details.platforms"), value: game.platforms.join(", ") })
    var rating = game.rating
    if (rating === null || rating === undefined) rating = root.selectedGame.rating
    if (typeof rating === "number" && isFinite(rating) && rating >= 0 && rating <= 100)
      rows.push({ label: strings.t("details.rating"), value: root.monthlyRatingLabel(rating) })
    return rows
  }

  function coverSource(game, useRemoteFallback) {
    var remote = root.safeCoverUrl(game && game.cover_url)
    if (!remote) return ""
    var local = root.safeCachedCoverUrl(game && game.cover_cache_url, remote)
    return useRemoteFallback || !local ? remote : local
  }

  function tooltipTitle(value) {
    // ToolTip formats markup automatically, so escape the untrusted title first.
    return String(value || "").substring(0, 160)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }

  function remainingLabel(days) {
    if (days === 0) return strings.t("panel.today")
    return strings.tn("panel.daysRemaining", days).arg(Commons.I18n.formatNumber(days))
  }

  function daysUntil(value) {
    var parts = String(value).split("-")
    var releaseDay = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
    var todayDay = Date.UTC(root.today.getFullYear(), root.today.getMonth(), root.today.getDate())
    return Math.max(0, Math.round((releaseDay - todayDay) / 86400000))
  }

  function updatedLabel() {
    var parsed = new Date(root.updatedAt)
    var timestamp = root.updatedAt && !isNaN(parsed.getTime())
      ? parsed.toLocaleString(Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat)
      : ""
    return timestamp === "" ? "" : strings.t(root.cached ? "panel.cachedAt" : "panel.fetchedAt").arg(timestamp)
  }

  Process {
    id: fetchProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleOutput(String(text || ""))
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) Qt.callLater(function() {
        if (root.loading) root.handleOutput("")
      })
    }
  }

  Process {
    id: monthlyProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleMonthlyOutput(String(text || ""), root.monthlyRequestKey)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) Qt.callLater(function() {
        if (root.monthlyLoading)
          root.handleMonthlyOutput("", root.monthlyRequestKey)
      })
    }
  }

  Process { id: gameOpenProcess }

  Process {
    id: detailProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleDetailOutput(String(text || ""))
    }
    onExited: function(exitCode) {
      Qt.callLater(function() {
        if (!root.selectedGame) return
        if (root.selectedGame.id !== root.detailProcessGameId) {
          root.startDetailFetch()
        } else if (exitCode !== 0 && root.detailLoading) {
          root.handleDetailOutput("")
        }
      })
    }
  }

  Timer {
    interval: 60000
    repeat: true
    running: true
    onTriggered: root.today = new Date()
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: popup.loadedContent
    contentWidth: popup.fittedContentWidth(Style.space(420))
    contentHeight: popup.fittedContentHeight(popup.loadedContentHeight, Style.space(560))

    content: PanelKeyCatcher {
      id: keyCatcher
      property alias listScroll: panelScroll
      property alias detailScroll: detailsScroll
      implicitHeight: root.detailsVisible
        ? Math.max(Style.space(340), detailsColumn.implicitHeight) : panelColumn.implicitHeight
      anchors.fill: parent
      onCloseRequested: root.close()
      onActivateRequested: root.activeTab === 0 ? root.refresh() : root.refreshMonthly(true)
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(key) {
        if (key === "r" || key === "R") {
          if (root.activeTab === 0) root.refresh()
          else root.refreshMonthly(true)
        }
      }

      Flickable {
        id: panelScroll
        visible: !root.detailsVisible
        anchors.fill: parent
        contentWidth: width
        contentHeight: panelColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: panelColumn
          width: panelScroll.width
          spacing: Style.space(12)

          Row {
            width: parent.width
            spacing: Style.space(6)

            Button {
              width: (parent.width - parent.spacing) / 2
              text: strings.t("panel.tabUpcoming")
              fontSize: Style.font.bodySmall
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              selected: root.activeTab === 0
              onClicked: root.selectTab(0)
            }

            Button {
              width: (parent.width - parent.spacing) / 2
              text: strings.t("monthly.tabTitle")
              fontSize: Style.font.bodySmall
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              selected: root.activeTab === 1
              onClicked: root.selectTab(1)
            }
          }

          Column {
            id: upcomingPage
            visible: root.activeTab === 0
            width: parent.width
            spacing: Style.space(12)

            Row {
            width: parent.width
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              text: strings.t("panel.title")
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              verticalAlignment: Text.AlignVCenter
              width: parent.width - refreshButton.implicitWidth - parent.spacing
              wrapMode: Text.WordWrap
            }

            Button {
              id: refreshButton
              text: strings.t("panel.refresh")
              iconText: "󰑐"
              iconSpinning: root.loading
              tooltipText: strings.t("panel.refreshTooltip")
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              enabled: !root.loading
              focusable: true
              onClicked: root.refresh()
            }
          }

          Text {
            visible: root.loading && root.games.length === 0
            textFormat: Text.PlainText
            text: strings.t("panel.loading")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
          }

          Text {
            visible: root.loading && root.games.length > 0
            textFormat: Text.PlainText
            text: strings.t("panel.refreshing")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
          }

          Text {
            visible: root.failed
            textFormat: Text.PlainText
            text: strings.t(root.credentialsMissing ? "panel.credentialsMissing" : "panel.error")
            color: Commons.Color.urgent
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.hasLoaded && !root.loading && !root.failed && root.games.length === 0
            textFormat: Text.PlainText
            text: strings.t("panel.empty")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.updatedAt !== ""
            textFormat: Text.PlainText
            text: root.updatedLabel()
            color: Qt.darker(root.contentForeground, 1.5)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            width: parent.width
          }

          Column {
            visible: root.games.length > 0
            width: parent.width
            spacing: Style.space(8)

            Repeater {
              model: root.games

              delegate: Column {
                required property var modelData
                required property int index
                width: parent.width
                spacing: Style.space(8)

                Rectangle {
                  id: upcomingRowSurface
                  width: parent.width
                  height: 62 + Style.space(6)
                  radius: Style.space(6)
                  color: upcomingRowHover.hovered
                    ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.10)
                    : "transparent"

                  Controls.ToolTip.text: strings.t("panel.gameDetailsTooltip")
                  Controls.ToolTip.visible: upcomingRowHover.hovered
                  Controls.ToolTip.delay: 500

                  Row {
                    id: upcomingRow
                    anchors.fill: parent
                    anchors.margins: Style.space(3)
                    spacing: Style.space(10)

                  Rectangle {
                    width: 44
                    height: 62
                    radius: Style.space(4)
                    color: Qt.darker(root.contentForeground, 2.4)
                    border.width: Math.max(1, Style.space(1))
                    border.color: Qt.darker(root.contentForeground, 1.8)
                    clip: true

                    Image {
                      id: upcomingCover
                      anchors.fill: parent
                      anchors.margins: Math.max(1, Style.space(1))
                      source: root.coverSource(
                        modelData,
                        upcomingCover.status === Image.Error
                          && root.safeCachedCoverUrl(modelData.cover_cache_url, modelData.cover_url) !== ""
                      )
                      sourceSize: Qt.size(90, 128)
                      asynchronous: true
                      cache: true
                      smooth: true
                      fillMode: Image.PreserveAspectFit
                      visible: status === Image.Ready
                    }

                    Image {
                      anchors.centerIn: parent
                      width: Style.space(24)
                      height: Style.space(24)
                      source: Qt.resolvedUrl("ps5-logo.svg")
                      sourceSize: Qt.size(48, 48)
                      fillMode: Image.PreserveAspectFit
                      asynchronous: true
                      visible: upcomingCover.status !== Image.Ready
                    }
                  }

                  Column {
                    width: parent.width - 44 - parent.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(6)

                    Text {
                      id: upcomingTitle
                      textFormat: Text.PlainText
                      text: String(modelData.name).substring(0, 160)
                      color: root.contentForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                      wrapMode: Text.NoWrap
                      elide: Text.ElideRight
                      width: parent.width
                      Controls.ToolTip.text: root.tooltipTitle(modelData.name)
                      Controls.ToolTip.visible: upcomingTitleHover.hovered
                      Controls.ToolTip.delay: 500

                      HoverHandler { id: upcomingTitleHover }
                    }

                    Row {
                      spacing: Style.space(7)

                      Text {
                        textFormat: Text.PlainText
                        text: root.formattedDate(modelData.released)
                        color: Qt.darker(root.contentForeground, 1.35)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: "·"
                        color: Qt.darker(root.contentForeground, 1.5)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: root.remainingLabel(root.daysUntil(modelData.released))
                        color: Commons.Color.accent
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }
                    }
                  }
                  }

                  HoverHandler {
                    id: upcomingRowHover
                    cursorShape: Qt.PointingHandCursor
                  }

                  TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onTapped: root.showDetails(modelData)
                  }
                }

                Rectangle {
                  visible: parent.index < root.games.length - 1
                  width: parent.width
                  height: Math.max(1, Style.space(1))
                  color: Qt.darker(root.contentForeground, 2.0)
                }
              }
            }
          }
        }

        Column {
          id: monthlyPage
          visible: root.activeTab === 1
          width: parent.width
          spacing: Style.space(10)

          Row {
            width: parent.width
            spacing: Style.space(4)

            PanelActionButton {
              id: previousMonth
              iconText: Commons.I18n.rightToLeft ? "󰅂" : "󰅁"
              tooltipText: strings.t("monthly.previousMonth")
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              enabled: !root.monthlyLoading
              onClicked: root.moveMonth(-1)
            }

            Text {
              textFormat: Text.PlainText
              text: root.monthLabel()
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              horizontalAlignment: Text.AlignHCenter
              verticalAlignment: Text.AlignVCenter
              width: parent.width - previousMonth.implicitWidth - nextMonth.implicitWidth
                - monthlyRefreshButton.implicitWidth - parent.spacing * 3
              wrapMode: Text.NoWrap
            }

            PanelActionButton {
              id: nextMonth
              iconText: Commons.I18n.rightToLeft ? "󰅁" : "󰅂"
              tooltipText: strings.t("monthly.nextMonth")
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              enabled: !root.monthlyLoading
              onClicked: root.moveMonth(1)
            }

            Button {
              id: monthlyRefreshButton
              text: strings.t("panel.refresh")
              iconText: "󰑐"
              iconSpinning: root.monthlyLoading
              tooltipText: strings.t("monthly.refreshTooltip")
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              enabled: !root.monthlyLoading
              focusable: true
              onClicked: root.refreshMonthly(true)
            }
          }

          Text {
            textFormat: Text.PlainText
            text: strings.t("monthly.rankingNote")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyUnrankedCount > 0
            textFormat: Text.PlainText
            text: strings.tn("monthly.unrankedCount", root.monthlyUnrankedCount)
              .arg(Commons.I18n.formatNumber(root.monthlyUnrankedCount))
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyLoading && root.monthlyGames.length === 0
            textFormat: Text.PlainText
            text: strings.t("monthly.loading")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyLoading && root.monthlyGames.length > 0
            textFormat: Text.PlainText
            text: strings.t("monthly.refreshing")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyFailed
            textFormat: Text.PlainText
            text: strings.t(root.monthlyCredentialsMissing ? "monthly.credentialsMissing" : "monthly.error")
            color: Commons.Color.urgent
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyHasLoaded && !root.monthlyLoading
              && !root.monthlyFailed && root.monthlyGames.length === 0
            textFormat: Text.PlainText
            text: strings.t("monthly.empty")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            width: parent.width
          }

          Text {
            visible: root.monthlyUpdatedAt !== ""
            textFormat: Text.PlainText
            text: root.monthlyUpdatedLabel()
            color: Qt.darker(root.contentForeground, 1.5)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            width: parent.width
          }

          Column {
            visible: root.monthlyGames.length > 0
            width: parent.width
            spacing: Style.space(8)

            Repeater {
              model: root.monthlyGames

              delegate: Column {
                required property var modelData
                required property int index
                width: parent.width
                spacing: Style.space(8)

                Rectangle {
                  id: monthlyRowSurface
                  width: parent.width
                  height: 62 + Style.space(6)
                  radius: Style.space(6)
                  color: monthlyRowHover.hovered
                    ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.10)
                    : "transparent"

                  Controls.ToolTip.text: strings.t("panel.gameDetailsTooltip")
                  Controls.ToolTip.visible: monthlyRowHover.hovered
                  Controls.ToolTip.delay: 500

                  Row {
                    id: monthlyRow
                    anchors.fill: parent
                    anchors.margins: Style.space(3)
                    spacing: Style.space(10)

                  Rectangle {
                    width: 44
                    height: 62
                    radius: Style.space(4)
                    color: Qt.darker(root.contentForeground, 2.4)
                    border.width: Math.max(1, Style.space(1))
                    border.color: Qt.darker(root.contentForeground, 1.8)
                    clip: true

                    Image {
                      id: monthlyCover
                      anchors.fill: parent
                      anchors.margins: Math.max(1, Style.space(1))
                      source: root.coverSource(
                        modelData,
                        monthlyCover.status === Image.Error
                          && root.safeCachedCoverUrl(modelData.cover_cache_url, modelData.cover_url) !== ""
                      )
                      sourceSize: Qt.size(90, 128)
                      asynchronous: true
                      cache: true
                      smooth: true
                      fillMode: Image.PreserveAspectFit
                      visible: status === Image.Ready
                    }

                    Image {
                      anchors.centerIn: parent
                      width: Style.space(24)
                      height: Style.space(24)
                      source: Qt.resolvedUrl("ps5-logo.svg")
                      sourceSize: Qt.size(48, 48)
                      fillMode: Image.PreserveAspectFit
                      asynchronous: true
                      visible: monthlyCover.status !== Image.Ready
                    }
                  }

                  Column {
                    width: parent.width - 44 - parent.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(6)

                    Text {
                      id: monthlyTitle
                      textFormat: Text.PlainText
                      text: String(modelData.name).substring(0, 160)
                      color: root.contentForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                      wrapMode: Text.NoWrap
                      elide: Text.ElideRight
                      width: parent.width
                      Controls.ToolTip.text: root.tooltipTitle(modelData.name)
                      Controls.ToolTip.visible: monthlyTitleHover.hovered
                      Controls.ToolTip.delay: 500

                      HoverHandler { id: monthlyTitleHover }
                    }

                    Row {
                      spacing: Style.space(7)

                      Text {
                        textFormat: Text.PlainText
                        text: root.monthlyRatingLabel(modelData.rating)
                        color: Commons.Color.accent
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: "·"
                        color: Qt.darker(root.contentForeground, 1.5)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: root.formattedDate(modelData.released)
                        color: Qt.darker(root.contentForeground, 1.35)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.bodySmall
                      }
                    }
                  }
                  }

                  HoverHandler {
                    id: monthlyRowHover
                    cursorShape: Qt.PointingHandCursor
                  }

                  TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onTapped: root.showDetails(modelData)
                  }
                }

                Rectangle {
                  visible: parent.index < root.monthlyGames.length - 1
                  width: parent.width
                  height: Math.max(1, Style.space(1))
                  color: Qt.darker(root.contentForeground, 2.0)
                }
              }
            }
          }
        }
      }
      }

      Flickable {
        id: detailsScroll
        visible: root.detailsVisible
        onVisibleChanged: if (visible) detailHeroImage.useFallback = false
        anchors.fill: parent
        contentWidth: width
        contentHeight: detailsColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: detailsColumn
          width: detailsScroll.width
          spacing: Style.space(12)

          Row {
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: strings.t("details.back")
              iconText: Commons.I18n.rightToLeft ? "󰅂" : "󰅁"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.backToList()
            }

            Text {
              textFormat: Text.PlainText
              text: strings.t("details.title")
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          Rectangle {
            id: detailHero
            width: parent.width
            height: Style.space(200)
            radius: Style.space(7)
            color: Qt.darker(root.contentForeground, 2.8)
            border.width: Math.max(1, Style.space(1))
            border.color: Qt.darker(root.contentForeground, 1.8)
            clip: true

            Image {
              id: detailHeroImage
              property bool useFallback: false
              anchors.fill: parent
              anchors.margins: Style.space(2)
              source: root.detailHeroSource(useFallback)
              onStatusChanged: if (status === Image.Error && !useFallback) useFallback = true
              sourceSize: Qt.size(569, 374)
              asynchronous: true
              cache: true
              smooth: true
              fillMode: Image.PreserveAspectFit
              visible: status === Image.Ready
            }

            Image {
              anchors.centerIn: parent
              width: Style.space(52)
              height: Style.space(52)
              source: Qt.resolvedUrl("ps5-logo.svg")
              sourceSize: Qt.size(104, 104)
              fillMode: Image.PreserveAspectFit
              asynchronous: true
              visible: detailHeroImage.status !== Image.Ready
            }
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: root.detailData ? root.detailData.name : (root.selectedGame ? root.selectedGame.name : "")
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            wrapMode: Text.Wrap
          }

          Column {
            width: parent.width
            spacing: Style.space(7)

            Repeater {
              model: root.detailMetaRows()

              delegate: Row {
                required property var modelData
                width: parent.width
                spacing: Style.space(8)

                Text {
                  width: Style.space(96)
                  textFormat: Text.PlainText
                  text: modelData.label
                  color: Qt.darker(root.contentForeground, 1.35)
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.Wrap
                }

                Text {
                  width: parent.width - Style.space(96) - parent.spacing
                  textFormat: Text.PlainText
                  text: modelData.value
                  color: root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.Wrap
                }
              }
            }
          }

          Text {
            visible: root.detailLoading
            width: parent.width
            textFormat: Text.PlainText
            text: strings.t("details.loading")
            color: Qt.darker(root.contentForeground, 1.35)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
          }

          Column {
            visible: root.detailFailed
            width: parent.width
            spacing: Style.space(8)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: strings.t(root.detailCredentialsMissing ? "details.credentialsMissing" : "details.error")
              color: Commons.Color.urgent
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
            }

            Button {
              text: strings.t("details.retry")
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: {
                root.detailLoading = true
                root.detailFailed = false
                root.startDetailFetch()
              }
            }
          }

          Column {
            visible: !root.detailLoading && !root.detailFailed
            width: parent.width
            spacing: Style.space(6)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: strings.t("details.about")
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.detailData ? (root.detailData.summary || root.detailData.storyline || strings.t("details.noDescription")) : ""
              color: Qt.darker(root.contentForeground, 1.15)
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
            }
          }

          Button {
            readonly property string destination: root.safeGameUrl(
              root.detailData && root.detailData.game_url ? root.detailData.game_url
                : root.selectedGame && root.selectedGame.game_url)
            text: strings.t("details.viewOnIgdb")
            iconText: "󰖟"
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            enabled: destination !== ""
            onClicked: root.openGame(destination)
          }
        }
      }
    }
  }
}
