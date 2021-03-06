from defcon.tools.notifications import NotificationCenter
from PyQt5.Qt import PYQT_VERSION_STR, QT_VERSION_STR
from PyQt5.QtCore import QEvent, QSize, QStandardPaths, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAction, QApplication, QDialogButtonBox, QFileDialog, QMessageBox)
from trufont import __version__
from trufont.drawingTools.selectionTool import SelectionTool
from trufont.drawingTools.penTool import PenTool
from trufont.drawingTools.rulerTool import RulerTool
from trufont.drawingTools.knifeTool import KnifeTool
from trufont.windows.fontWindow import FontWindow
from trufont.windows.inspectorWindow import InspectorWindow
from trufont.windows.scriptingWindow import ScriptingWindow
from trufont.objects import settings
from trufont.objects.defcon import TFont
from trufont.objects.menu import (
    Entries, MAX_RECENT_FILES, globalMenuBar, MenuBar)
from trufont.tools import errorReports, glyphList, platformSpecific
import os
import platform
import subprocess

try:
    gitShortHash = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'], stderr=subprocess.DEVNULL
    ).decode()
except:
    gitShortHash = ""


class Application(QApplication):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._currentGlyph = None
        self._currentMainWindow = None
        self._launched = False
        self._drawingTools = [SelectionTool, PenTool, RulerTool, KnifeTool]
        self._extensions = []
        self.dispatcher = NotificationCenter()
        self.dispatcher.addObserver(self, "_mainWindowClosed", "fontWillClose")
        # TODO: see about filtering this into windowChanged
        # except if we're going to use tabs with changing menu, then it might
        # be convenient to no filter out (no additional signal has to be sent)
        self.focusChanged.connect(self._focusWidgetChanged)
        self.GL2UV = None
        self.inspectorWindow = None
        self.outputWindow = None

    # --------------
    # Event handling
    # --------------

    def _focusWidgetChanged(self):
        # update menu bar
        self.updateMenuBar()
        # update main window
        window = self.activeWindow()
        if window is None:
            return
        while True:
            parent = window.parent()
            if parent is None:
                break
            window = parent
        if isinstance(window, FontWindow):
            self.setCurrentMainWindow(window)

    def _mainWindowClosed(self, notification):
        font = notification.data["font"]
        # cleanup CurrentFont/CurrentGlyph when closing the corresponding
        # window
        if self._currentMainWindow is not None:
            if self._currentMainWindow.font == font:
                self.setCurrentMainWindow(None)
        if self._currentGlyph is not None:
            if self._currentGlyph.font == font:
                self.setCurrentGlyph(None)

    def event(self, event):
        eventType = event.type()
        # respond to OSX open events
        if eventType == QEvent.FileOpen:
            filePath = event.file()
            self.openFile(filePath)
            return True
        elif eventType == QEvent.ApplicationStateChange:
            applicationState = self.applicationState()
            if applicationState == Qt.ApplicationActive:
                if not self._launched:
                    notification = "applicationLaunched"
                    self.loadGlyphList()
                    self._launched = True
                else:
                    notification = "applicationActivated"
                    # XXX: do it
                    # self.lookupExternalChanges()
                self.postNotification(notification)
            elif applicationState == Qt.ApplicationInactive:
                self.postNotification("applicationWillIdle")
        return super().event(event)

    def postNotification(self, notification, data=None):
        dispatcher = self.dispatcher
        dispatcher.postNotification(
            notification=notification, observable=self, data=data)

    # ---------------
    # File management
    # ---------------

    def loadGlyphList(self):
        glyphListPath = settings.glyphListPath()
        if glyphListPath and os.path.exists(glyphListPath):
            try:
                glyphList_ = glyphList.parseGlyphList(glyphListPath)
            except Exception as e:
                msg = self.tr(
                    "The glyph list at {0} cannot "
                    "be parsed and will be dropped.").format(glyphListPath)
                errorReports.showWarningException(e, msg)
                settings.removeGlyphListPath()
            else:
                self.GL2UV = glyphList_

    def lookupExternalChanges(self):
        for font in self.allFonts():
            if not font.path:
                continue
            changed = font.testForExternalChanges()
            for attr in ("info", "kerning", "groups", "features", "lib"):
                if changed[attr]:
                    data = dict(font=font)
                    self.postNotification("fontChangedExternally", data)
                    return
            # XXX: do more

    # -----------------
    # Window management
    # -----------------

    def currentMainWindow(self):
        return self._currentMainWindow

    def setCurrentMainWindow(self, mainWindow):
        if mainWindow == self._currentMainWindow:
            return
        self._currentMainWindow = mainWindow
        self.postNotification("currentFontChanged")

    def openMetricsWindow(self, font):
        # TODO: why are we doing this for metrics window and no other child
        # window?
        for widget in self.topLevelWidgets():
            if isinstance(widget, FontWindow) and widget.font_() == font:
                widget.metrics()
                return widget._metricsWindow
        return None

    # --------
    # Menu Bar
    # --------

    def fetchMenuBar(self, parent=None):
        if platformSpecific.useGlobalMenuBar():
            try:
                self._menuBar
            except:
                self._menuBar = globalMenuBar()
            self._menuBar.resetState()
            return self._menuBar
        return MenuBar(parent)

    def setupMenuBar(self, menuBar=None):
        if menuBar is None:
            try:
                menuBar = self._menuBar
            except:
                return
            menuBar.resetState()
        activeWindow = self.activeWindow()
        # XXX: on local menu bar, entries shouldnt be activated if they werent
        # fetched by local
        # in that case, maybe return a parentless action, that isnt added to
        # the menu
        fileMenu = menuBar.fetchMenu(Entries.File)
        fileMenu.fetchAction(Entries.File_New, self.newFile)
        fileMenu.fetchAction(Entries.File_Open, self.openFile)
        # TODO: maybe move save in there and add save all and close
        recentFilesMenu = fileMenu.fetchMenu(Entries.File_Open_Recent)
        self.updateRecentFiles(recentFilesMenu)
        fileMenu.fetchAction(Entries.File_Exit, self.exit)

        scriptsMenu = menuBar.fetchMenu(Entries.Scripts)
        self.updateExtensions(scriptsMenu)

        windowMenu = menuBar.fetchMenu(Entries.Window)
        if platformSpecific.windowCommandsInMenu(
                ) and activeWindow is not None:
            windowMenu.fetchAction(
                Entries.Window_Minimize, activeWindow.showMinimized)
            windowMenu.fetchAction(
                Entries.Window_Minimize_All, self.minimizeAll)
            windowMenu.fetchAction(
                Entries.Window_Zoom, lambda: self.zoom(activeWindow))
        windowMenu.fetchAction(Entries.Window_Inspector, self.inspector)
        windowMenu.fetchAction(Entries.Window_Scripting, self.scripting)
        if self.outputWindow is not None:
            windowMenu.fetchAction(
                Entries.Window_Output, self.output)
        # TODO: add a list of open windows in window menu, check active window
        # maybe add helper function that filters topLevelWidgets into windows
        # bc we need this in a few places

        helpMenu = menuBar.fetchMenu(Entries.Help)
        helpMenu.fetchAction(
            Entries.Help_Documentation,
            lambda: QDesktopServices.openUrl(
                QUrl("http://trufont.github.io/")))
        helpMenu.fetchAction(
            Entries.Help_Report_An_Issue,
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/trufont/trufont/issues/new")))
        helpMenu.addSeparator()
        helpMenu.fetchAction(Entries.Help_About, self.about)

    def updateMenuBar(self):
        window = self.activeWindow()
        if window is None:
            self.setupMenuBar()
            return
        # update menu
        if hasattr(window, "setupMenu"):
            # TODO: convoluted. try to reduce the number of calls
            menuBar = self.fetchMenuBar(window)
            window.setupMenu(menuBar)
            menuBar.setSpawnElementsHint(False)
            self.setupMenuBar(menuBar)
            menuBar.setSpawnElementsHint(True)
            window.setMenuBar(menuBar)
        else:
            self.setupMenuBar()

    # ---------
    # Scripting
    # ---------

    def allFonts(self):
        fonts = []
        for widget in self.topLevelWidgets():
            if isinstance(widget, FontWindow):
                font = widget.font_()
                fonts.append(font)
        return

    def currentFont(self):
        # might be None when closing all windows with scripting window open
        if self._currentMainWindow is None:
            return None
        return self._currentMainWindow.font_()

    def currentGlyph(self):
        return self._currentGlyph

    def setCurrentGlyph(self, glyph):
        if glyph == self._currentGlyph:
            return
        self._currentGlyph = glyph
        self.postNotification("currentGlyphChanged")

    def globals(self):
        global_vars = {
            "__builtins__": __builtins__,
            "AllFonts": self.allFonts,
            "CurrentFont": self.currentFont,
            "CurrentGlyph": self.currentGlyph,
            "events": self.dispatcher,
            "registerTool": self.registerTool,
            "OpenMetricsWindow": self.openMetricsWindow,
            "qApp": self,
        }
        return global_vars

    # directory getters

    def _getLocalDirectory(self, key, name):
        userPath = settings.value(key, type=str)
        if userPath and os.path.isdir(userPath):
            return userPath

        appDataFolder = QStandardPaths.standardLocations(
            QStandardPaths.AppLocalDataLocation)[0]
        subFolder = os.path.normpath(os.path.join(
            appDataFolder, name))

        if not os.path.exists(subFolder):
            try:
                os.makedirs(subFolder)
            except OSError:
                subFolder = os.path.expanduser("~")

        settings.setValue(key, subFolder)
        return subFolder

    def getExtensionsDirectory(self):
        return self._getLocalDirectory(
            "scripting/extensionsPath", "Extensions")

    def getScriptsDirectory(self):
        return self._getLocalDirectory("scripting/scriptsPath", "Scripts")

    # -------------
    # Drawing tools
    # -------------

    def drawingTools(self):
        return self._drawingTools

    def registerTool(self, tool):
        self._drawingTools.append(tool)
        data = dict(tool=tool)
        self.postNotification("drawingToolRegistered", data)

    def unregisterTool(self, tool):
        self._drawingTools.remove(tool)
        data = dict(tool=tool)
        self.postNotification("drawingToolUnregistered", data)

    # ----------
    # Extensions
    # ----------

    def extensions(self):
        return self._extensions

    def registerExtension(self, extension):
        self._extensions.append(extension)
        self.updateMenuBar()
        data = dict(extension=extension)
        self.postNotification("extensionRegistered", data)

    def unregisterExtension(self, extension):
        self._extensions.remove(extension)
        self.updateMenuBar()
        data = dict(extension=extension)
        self.postNotification("extensionUnregistered", data)

    def updateExtensions(self, menu):
        def getFunc(ext, path):
            # need a stack frame here to return a unique lambda for each run
            return lambda: ext.run(path)

        menu.clear()
        # also clear submenus
        for child in menu.children():
            if isinstance(child, menu.__class__):
                child.setParent(None)
                child.deleteLater()

        for extension in self._extensions:
            addToMenu = extension.addToMenu
            if addToMenu:
                if isinstance(addToMenu, list):
                    parentMenu = menu.addMenu(extension.name or "")
                else:
                    addToMenu = [addToMenu]
                    parentMenu = menu
                for entry in addToMenu:
                    menuName = entry.get("name")
                    menuPath = entry.get("path")
                    shortcut = entry.get("shortcut")
                    parentMenu.addAction(
                        menuName, getFunc(extension, menuPath), shortcut)
        menu.addSeparator()
        # TODO
        action = menu.addAction(self.tr(Entries.Scripts_Build_Extension))
        action.setEnabled(False)

    # ----------------
    # Menu Bar entries
    # ----------------

    def newFile(self):
        font = TFont.newStandardFont()
        window = FontWindow(font)
        window.show()

    def openFile(self, path=None):
        if not path:
            fileFormat = self.tr("UFO Fonts {}")
            if platformSpecific.treatPackageAsFile():
                ext = "(*.ufo)"
            else:
                ext = "(metainfo.plist)"

            path, _ = QFileDialog.getOpenFileName(
                self.activeWindow(), self.tr("Open File"), '',
                fileFormat.format(ext)
            )
            if not path:
                return
        if ".plist" in path:
            path = os.path.dirname(path)
        path = os.path.normpath(path)
        for widget in self.topLevelWidgets():
            if isinstance(widget, FontWindow):
                font = widget.font_()
                if font is not None and font.path == path:
                    widget.raise_()
                    return
        try:
            font = TFont(path)
            window = FontWindow(font)
        except Exception as e:
            msg = self.tr(
                "There was an issue when opening the font at {}.".format(
                    path))
            errorReports.showCriticalException(e, msg)
            return
        window.show()
        self.setCurrentFile(font.path)

    def openRecentFile(self):
        fontPath = self.sender().toolTip()
        self.openFile(fontPath)

    # Window

    def minimizeAll(self):
        for widget in self.topLevelWidgets():
            if widget.isVisible():
                # additional guard, shouldnt be needed
                # if isinstance(widget, (QMenu, QMenuBar)):
                #     continue
                widget.showMinimized()

    def zoom(self, window):
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def inspector(self):
        if self.inspectorWindow is None:
            self.inspectorWindow = InspectorWindow()
        if self.inspectorWindow.isVisible():
            # TODO: do this only if the widget is user-visible, otherwise the
            # key press feels as if it did nothing
            # toggle
            self.inspectorWindow.close()
        else:
            self.inspectorWindow.show()

    def scripting(self):
        # TODO: don't store, spawn window each time instead
        # or have tabs?
        if not hasattr(self, '_scriptingWindow'):
            scriptingWindow = ScriptingWindow()
            scriptingWindow.show()
        elif self._scriptingWindow.isVisible():
            self._scriptingWindow.raise_()
        else:
            self._scriptingWindow.show()

    def output(self):
        self.outputWindow.setVisible(not self.outputWindow.isVisible())

    # Help

    def about(self):
        name = self.applicationName()
        domain = self.organizationDomain()
        caption = self.tr(
            "<h3>About {n}</h3>"
            "<p>{n} is a cross-platform, modular typeface design "
            "application.</p>").format(n=name)
        text = self.tr(
            "<p>{} is built on top of "
            "<a href='http://ts-defcon.readthedocs.org/en/ufo3/'>defcon</a> "
            "and includes scripting support "
            "with a <a href='http://robofab.com/'>robofab</a>-like API.</p>"
            "<p>Running on Qt {} (PyQt {}).</p>"
            "<p>Version {} {} – Python {}.").format(
            name, QT_VERSION_STR, PYQT_VERSION_STR, __version__, gitShortHash,
            platform.python_version())
        if domain:
            text += self.tr("<br>See <a href='http://{d}'>{d}</a> for more "
                            "information.</p>").format(d=domain)
        else:
            text += "</p>"
        # This duplicates much of QMessageBox.about(), but it has no way to
        # setInformativeText()...
        msgBox = QMessageBox(self.activeWindow())
        msgBox.setAttribute(Qt.WA_DeleteOnClose)
        icon = msgBox.windowIcon()
        size = icon.actualSize(QSize(64, 64))
        msgBox.setIconPixmap(icon.pixmap(size))
        msgBox.setWindowTitle(self.tr("About {}").format(name))
        msgBox.setText(caption)
        msgBox.setInformativeText(text)
        if platformSpecific.useCenteredButtons():
            buttonBox = msgBox.findChild(QDialogButtonBox)
            buttonBox.setCenterButtons(True)
        msgBox.show()

    # ------------
    # Recent files
    # ------------

    def setCurrentFile(self, path):
        if path is None:
            return
        path = os.path.abspath(path)
        recentFiles = settings.recentFiles()
        if path in recentFiles:
            recentFiles.remove(path)
        recentFiles.insert(0, path)
        while len(recentFiles) > MAX_RECENT_FILES:
            del recentFiles[-1]
        settings.setRecentFiles(recentFiles)

    def updateRecentFiles(self, menu):
        # bootstrap
        actions = menu.actions()
        for i in range(MAX_RECENT_FILES):
            try:
                action = actions[i]
            except IndexError:
                action = QAction(menu)
                menu.addAction(action)
            action.setVisible(False)
            action.triggered.connect(self.openRecentFile)
        # fill
        actions = menu.actions()
        recentFiles = settings.recentFiles()
        count = min(len(recentFiles), MAX_RECENT_FILES)
        for index, recentFile in enumerate(recentFiles[:count]):
            action = actions[index]
            shortName = os.path.basename(recentFile.rstrip(os.sep))

            action.setText(shortName)
            action.setToolTip(recentFile)
            action.setVisible(True)
        for index in range(count, MAX_RECENT_FILES):
            actions[index].setVisible(False)

        menu.setEnabled(len(recentFiles))
