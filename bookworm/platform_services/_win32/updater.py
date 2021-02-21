# coding: utf-8

import sys
import os
import shutil
import zipfile
import tempfile
import wx
import win32api
from hashlib import sha1
from pathlib import Path
from System.Diagnostics import Process
from requests.exceptions import RequestException
from bookworm import app
from bookworm import config
from bookworm import paths
from bookworm.http_tools import HttpResource
from bookworm.utils import generate_sha1hash
from bookworm.logger import logger


log = logger.getChild(__name__)


def kill_other_running_instances():
    """Ensure that only this instance is running."""
    log.debug("Killing other running instances of the application.")
    pid, exe_dir = os.getpid(), Path(sys.executable).resolve().parent
    for proc in Process.GetProcessesByName(app.name):
        if Path(proc.MainModule.FileName).resolve().parent != exe_dir:
            continue
        if proc.Id != os.getpid():
            proc.Kill()


def extract_update_bundle(bundle):
    past_update_dir = paths.data_path("update")
    if past_update_dir.exists():
        log.info("Found previous update data. Removing...")
        shutil.rmtree(past_update_dir, ignore_errors=True)
    log.debug("Extracting update bundle")
    extraction_dir = paths.data_path("update", "extracted")
    extraction_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle, compression=zipfile.ZIP_LZMA) as archive:
        archive.extractall(extraction_dir)
    return extraction_dir


def perform_update(upstream_version_info):
    msg = wx.MessageBox(
        # Translators: the content of a message indicating the availability of an update
        _(
            "A new update for Bookworm has been released.\n"
            "Would you like to download and install it?\n"
            "\tInstalled Version: {current}\n"
            "\tNew Version: {new}\n"
        ).format(current=app.version, new=upstream_version_info.version),
        # Translators: the title of a message indicating the availability of an update
        _("Bookworm Update"),
        style=wx.YES_NO | wx.ICON_INFORMATION,
    )
    if msg != wx.YES:
        log.info("User cancelled the update.")
        return
    # Download the update package
    progress_dlg = wx.ProgressDialog(
        # Translators: the title of a message indicating the progress of downloading an update
        _("Downloading Update"),
        # Translators: a message indicating the progress of downloading an update bundle
        _("Downloading {url}:").format(url=upstream_version_info.bundle_download_url),
        maximum=100,
        parent=wx.GetApp().mainFrame,
        style=wx.PD_APP_MODAL | wx.PD_REMAINING_TIME | wx.PD_AUTO_HIDE,
    )
    bundle_file = tempfile.TemporaryFile()
    try:
        log.debug(f"Downloading update from: {upstream_version_info.bundle_download_url}")
        dl_request = HttpResource(upstream_version_info.bundle_download_url).download()
        callback = lambda prog: wx.CallAfter(
            progress_dlg.Update,
            prog.percentage,
            _("Downloaded {downloaded} MB of {total} MB"
            ).format(downloaded=prog.downloaded_mb, total=prog.total_mb)
        )
        dl_request.download_to_file(bundle_file, callback)
    except ConnectionError:
        log.exception("Failed to download update file")
        wx.CallAfter(
            wx.MessageBox,
            # Translators: the content of a message indicating a failure in downloading an update
            _(
                "A network error was occured when trying to download the update.\n"
                "Make sure you are connected to the internet, "
                "or try again at a later time."
            ),
            # Translators: the title of a message indicating a failure in downloading an update
            _("Network Error"),
            style=wx.ICON_ERROR,
        )
        return
    finally:
        wx.CallAfter(progress_dlg.Hide)
        wx.CallAfter(progress_dlg.Destroy)
    log.debug("The update bundle has been downloaded successfully.")
    if generate_sha1hash(bundle_file) != upstream_version_info.update_sha1hash:
        log.debug("Hashes do not match.")
        bundle_file.close()
        msg = wx.MessageBox(
            # Translators: the content of a message indicating a corrupted file
            _(
                "The update file has been downloaded, but it has been corrupted during download.\n"
                "Would you like to download the update file again?"
            ),
            # Translators: the title of a message indicating a corrupted file
            _("Download Error"),
            style=wx.YES_NO | wx.ICON_QUESTION,
        )
        if msg == wx.YES:
            return perform_update(upstream_version_info)
        else:
            return
    # Go ahead and install the update
    log.debug("Installing the update...")
    wx.MessageBox(
        # Translators: the content of a message indicating successful download of the update bundle
        _(
            "The update has been downloaded successfully, and it is ready to be installed.\n"
            "The application will be restarted in order to complete the update process.\n"
            "Click the OK button to continue."
        ),
        # Translators: the title of a message indicating successful download of the update bundle
        _("Download Completed"),
        style=wx.ICON_INFORMATION,
    )
    ex_dlg = wx.ProgressDialog(
        # Translators: the title of a message shown when extracting an update bundle
        _("Extracting Update Bundle"),
        # Translators: a message shown when extracting an update bundle
        _("Please wait..."),
        parent=wx.GetApp().mainFrame,
        style=wx.PD_APP_MODAL,
    )
    bundle_file.seek(0)
    extraction_dir = extract_update_bundle(bundle_file)
    bundle_file.close()
    wx.CallAfter(ex_dlg.Close)
    wx.CallAfter(ex_dlg.Destroy)
    if extraction_dir is not None:
        wx.CallAfter(execute_bootstrap, extraction_dir)


def execute_bootstrap(extraction_dir):
    log.info("Executing bootstrap to complete update.")
    move_to = extraction_dir.parent
    shutil.move(str(extraction_dir / "bootstrap.exe"), str(move_to))
    args = f'"{os.getpid()}" "{extraction_dir}" "{paths.app_path()}" "{sys.executable}"'
    viewer = wx.GetApp().mainFrame
    if viewer.reader.ready:
        viewer.reader.save_current_position()
    kill_other_running_instances()
    win32api.ShellExecute(0, "open", str(move_to / "bootstrap.exe"), args, "", 5)
    log.info("Bootstrap has been executed.")
    sys.exit(0)