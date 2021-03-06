from defconQt import representationFactories as baseRepresentationFactories
from trufont import __version__, representationFactories
from trufont.objects.application import Application
from trufont.objects.extension import TExtension
from trufont.resources import icons_db  # noqa
from trufont.tools import errorReports, platformSpecific
from trufont.windows.outputWindow import OutputWindow
from PyQt5.QtCore import (
    Qt, QCommandLineParser, QSettings, QTranslator, QLocale, QLibraryInfo)
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication
import os
import sys


def main():
    global app
    # register representation factories
    baseRepresentationFactories.registerAllFactories()
    representationFactories.registerAllFactories()
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    # initialize the app
    app = Application(sys.argv)
    app.setOrganizationName("TruFont")
    app.setOrganizationDomain("trufont.github.io")
    app.setApplicationName("TruFont")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(":app.png"))
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setStyleSheet(platformSpecific.appStyleSheet())

    # Install stream redirection
    app.outputWindow = OutputWindow()
    # Exception handling
    sys.excepthook = errorReports.exceptionCallback

    # Qt's translation for itself. May not be installed.
    qtTranslator = QTranslator()
    qtTranslator.load("qt_" + QLocale.system().name(),
                      QLibraryInfo.location(QLibraryInfo.TranslationsPath))
    app.installTranslator(qtTranslator)

    appTranslator = QTranslator()
    appTranslator.load("trufont_" + QLocale.system().name(),
                       os.path.dirname(os.path.realpath(__file__)) +
                       "/resources")
    app.installTranslator(appTranslator)

    # parse options and open fonts
    parser = QCommandLineParser()
    parser.setApplicationDescription(QApplication.translate(
        "Command-line parser", "The TruFont font editor."))
    parser.addHelpOption()
    parser.addVersionOption()
    parser.addPositionalArgument(QApplication.translate(
        "Command-line parser", "files"), QApplication.translate(
        "Command-line parser", "The UFO files to open."))
    parser.process(app)
    # bootstrap extensions
    folder = app.getExtensionsDirectory()
    for file in os.listdir(folder):
        if not file.rstrip("\\/ ").endswith(".tfExt"):
            continue
        path = os.path.join(folder, file)
        try:
            extension = TExtension(path)
            if extension.launchAtStartup:
                extension.run()
        except Exception as e:
            msg = QApplication.translate(
                "Extensions", "The extension at {0} could not be run.".format(
                    path))
            errorReports.showWarningException(e, msg)
            continue
        app.registerExtension(extension)
    # load menu
    if platformSpecific.useGlobalMenuBar():
        menuBar = app.fetchMenuBar()  # noqa
        app.setQuitOnLastWindowClosed(False)
    # process files
    args = parser.positionalArguments()
    if not args:
        fontPath = None
        # maybe load recent file
        settings = QSettings()
        loadRecentFile = settings.value("misc/loadRecentFile", False, bool)
        if loadRecentFile:
            recentFiles = settings.value("core/recentFiles", [], type=str)
            if len(recentFiles) and os.path.exists(recentFiles[0]):
                fontPath = recentFiles[0]
                app.openFile(fontPath)
        # otherwise, create a new file
        if fontPath is None:
            app.newFile()
    else:
        for fontPath in args:
            app.openFile(fontPath)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
