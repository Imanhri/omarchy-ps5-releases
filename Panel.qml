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

  function openGame(value, process) {
    var safeUrl = root.safeGameUrl(value)
    if (!safeUrl || !process || process.running) return
    process.command = ["xdg-open", safeUrl]
    process.running = true
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
      implicitHeight: panelColumn.implicitHeight
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
                  property string validatedUrl: root.safeGameUrl(modelData.game_url)
                  width: parent.width
                  height: 62 + Style.space(6)
                  radius: Style.space(6)
                  color: upcomingRowHover.hovered && validatedUrl !== ""
                    ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.10)
                    : "transparent"

                  Controls.ToolTip.text: strings.t("panel.gameDetailsTooltip")
                  Controls.ToolTip.visible: upcomingRowHover.hovered && validatedUrl !== ""
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
                    cursorShape: upcomingRowSurface.validatedUrl !== ""
                      ? Qt.PointingHandCursor : Qt.ArrowCursor
                  }

                  TapHandler {
                    acceptedButtons: Qt.LeftButton
                    enabled: upcomingRowSurface.validatedUrl !== ""
                    onTapped: root.openGame(upcomingRowSurface.validatedUrl, upcomingOpenProcess)
                  }

                  Process { id: upcomingOpenProcess }
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
                  property string validatedUrl: root.safeGameUrl(modelData.game_url)
                  width: parent.width
                  height: 62 + Style.space(6)
                  radius: Style.space(6)
                  color: monthlyRowHover.hovered && validatedUrl !== ""
                    ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.10)
                    : "transparent"

                  Controls.ToolTip.text: strings.t("panel.gameDetailsTooltip")
                  Controls.ToolTip.visible: monthlyRowHover.hovered && validatedUrl !== ""
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
                    cursorShape: monthlyRowSurface.validatedUrl !== ""
                      ? Qt.PointingHandCursor : Qt.ArrowCursor
                  }

                  TapHandler {
                    acceptedButtons: Qt.LeftButton
                    enabled: monthlyRowSurface.validatedUrl !== ""
                    onTapped: root.openGame(monthlyRowSurface.validatedUrl, monthlyOpenProcess)
                  }

                  Process { id: monthlyOpenProcess }
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
  }
}
}
