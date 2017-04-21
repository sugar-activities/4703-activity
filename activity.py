# -*- coding: utf-8 -*-

# Copyright 2012 Daniel Drake
# Copyright 2012, 2013 Walter Bender
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

import os
import glob
import subprocess
import logging

import gobject
gobject.threads_init()

import gtk
from gtk import gdk
import gst
import gio
import glib

from gettext import gettext as _

from sugar.activity import activity
from sugar.graphics.toolbarbox import ToolbarBox
from sugar.graphics.toolbutton import ToolButton
from sugar.activity.widgets import ActivityToolbarButton
from sugar.activity.widgets import StopButton
from sugar.graphics import style

from videos import VIDEOS
from lessons import LESSONS


class PaddedVBox(gtk.VBox):
    __gtype_name__ = 'PaddedVBox'

    def do_size_allocate(self, allocation):
        allocation.width -= 20
        allocation.height -= 20 
        allocation.x += 10
        allocation.y += 10
        gtk.VBox.do_size_allocate(self, allocation)


class VideoPlayer(gtk.EventBox):
    def __init__(self):
        super(VideoPlayer, self).__init__()
        self.unset_flags(gtk.DOUBLE_BUFFERED)
        self.set_flags(gtk.APP_PAINTABLE)

        self._sink = None
        self._xid = None
        self.connect('realize', self.__realize)
        self.playing = False
        self.paused = False

        self._vpipeline = gst.element_factory_make('playbin2', 'vplayer')

        bus = self._vpipeline.get_bus()
        bus.enable_sync_message_emission()
        bus.add_signal_watch()
        bus.connect('sync-message::element', self.__on_sync_message)
        bus.connect('message', self.__on_vmessage)

    def __on_sync_message(self, bus, message):
        if message.structure is None:
            return
        if message.structure.get_name() == 'prepare-xwindow-id':
            message.src.set_property('force-aspect-ratio', True)
            self._sink = message.src
            self._sink.set_xwindow_id(self._xid)

    def __on_vmessage(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_EOS:
            self._vpipeline.seek_simple(
                gst.FORMAT_TIME, gst.SEEK_FLAG_FLUSH, 0)
            self.playing = False

    def __realize(self, widget):
        self._xid = self.window.xid

    def do_expose_event(self):
        if self._sink:
            self._sink.expose()
            return False
        else:
            return True

    def play(self, filename):
        if filename:
            path = os.path.join(activity.get_bundle_path(), 'video', filename)
            gfile = gio.File(path=path)
            self._vpipeline.set_property('uri', gfile.get_uri())

        ret = self._vpipeline.set_state(gst.STATE_PLAYING)
        self.playing = True
        self.paused = False

    def stop(self):
        self._vpipeline.set_state(gst.STATE_NULL)
        self.playing = False
        self.paused = False

    def pause(self):
        self._vpipeline.set_state(gst.STATE_PAUSED)
        self.paused = True
        self.playing = False

    def unpause(self):
        self._vpipeline.set_state(gst.STATE_PLAYING)
        self.paused = False


class VideoButton(gtk.EventBox):
    def __init__(self, title, image_path):
        super(VideoButton, self).__init__()
        self.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#000000'))
        self.connect('realize', self._eventbox_realized)
        self.connect('enter-notify-event', self._eventbox_entered)
        self.connect('leave-notify-event', self._eventbox_left)

        self._image_path = image_path
        self._last_width = 0
        self._last_height = 0

        self._frame = gtk.Frame()
        self._frame.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#000000'))
        self.add(self._frame)
        self._frame.show()

        self._vbox = gtk.VBox()
        self._frame.add(self._vbox)
        self._vbox.show()

        self._image = gtk.Image()
        self._image.connect('size-allocate', self._image_size_allocated)
        self._vbox.pack_start(self._image, expand=True, fill=True, padding=5)
        self._image.show()

        self._title = gtk.Label(title)
        self._title.modify_fg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#FFFFFF'))
        self._vbox.pack_start(self._title, expand=False, padding=5)
        self._title.show()

    def _image_size_allocated(self, widget, allocation):
        if not self._image_path:
            return False
        if self._last_width == allocation.width and \
           self._last_height == allocation.height:
            return False

        width = allocation.width
        self._last_width = width
        height = allocation.height
        self._last_height = height
        pixbuf = gdk.pixbuf_new_from_file_at_size(
            self._image_path, width, height)
        self._image.set_from_pixbuf(pixbuf)

    def _eventbox_entered(self, widget, event):
        self._frame.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#333333'))

    def _eventbox_left(self, widget, event):
        self._frame.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#000000'))

    def _eventbox_realized(self, widget):
        self.window.set_cursor(gdk.Cursor(gdk.HAND2))


class VideoPlayerActivity(activity.Activity):
    def __init__(self, handle):
        activity.Activity.__init__(self, handle)
        self._current_video_idx = 0
        self._lesson_state = False
        self.max_participants = 1

        # Set blackground as blue
        self.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#3FBDAC'))

        if hasattr(self, '_event_box'):
            # for pre-0.96
            self._event_box.modify_bg(
                gtk.STATE_NORMAL, gtk.gdk.color_parse('#3FBDAC'))

        toolbar_box = ToolbarBox()
        activity_button = ActivityToolbarButton(self)
        toolbar_box.toolbar.insert(activity_button, 0)
        activity_button.show()

        self._play_button = ToolButton('media-playback-start')
        self._play_button.set_tooltip(_('Play video'))
        self._play_button.connect('clicked', self._play_clicked)
        self._play_button.set_sensitive(True)
        self._play_button.show()
        toolbar_box.toolbar.insert(self._play_button, -1)

        self._pause_button = ToolButton('media-playback-pause')
        self._pause_button.set_tooltip(_('Pause video'))
        self._pause_button.connect('clicked', self._pause_clicked)
        self._pause_button.set_sensitive(False)
        self._pause_button.show()
        toolbar_box.toolbar.insert(self._pause_button, -1)

        self._stop_button = ToolButton('media-playback-stop')
        self._stop_button.set_tooltip(_('Stop video'))
        self._stop_button.connect('clicked', self._stop_clicked)
        self._stop_button.set_sensitive(False)
        self._stop_button.show()
        toolbar_box.toolbar.insert(self._stop_button, -1)

        self._lesson_button = ToolButton('view-list')
        self._lesson_button.connect('clicked', self._toggle_lesson)
        self._lesson_button.set_tooltip(_('View lesson'))
        self._lesson_button.set_sensitive(False)
        self._lesson_button.show()
        toolbar_box.toolbar.insert(self._lesson_button, -1)

        separator = gtk.SeparatorToolItem()
        separator.props.draw = False
        separator.set_expand(True)
        separator.show()
        toolbar_box.toolbar.insert(separator, -1)

        tool = StopButton(self)
        toolbar_box.toolbar.insert(tool, -1)
        tool.show()

        self.set_toolbox(toolbar_box)
        toolbar_box.show()

        vbox = PaddedVBox()
        vbox.show()
        self.set_canvas(vbox)

        self._menu = gtk.Table(2, 3, True)
        self._menu.set_row_spacings(10)
        self._menu.set_col_spacings(10)
        vbox.pack_start(self._menu, expand=True, fill=True)
        self._menu.show()

        self._videos = VIDEOS
        self._lessons = LESSONS

        self._generate_menu()

        self._video_title = gtk.Label()
        # self._video_title.modify_fg(
        #     gtk.STATE_NORMAL, gtk.gdk.color_parse('#FFFFFF'))
        vbox.pack_start(self._video_title, expand=False)

        self._video = VideoPlayer()
        vbox.pack_start(self._video, expand=True, fill=True, padding=10)
        self._video.realize()

        self._video_description = gtk.Label()
        self._video_description.set_text('\n\n\n\n')
        self._video_description.set_line_wrap(True)
        self._video_description.set_size_request(
            gtk.gdk.screen_width() - style.GRID_CELL_SIZE * 2, -1)
        # self._video_description.modify_fg(
        #     gtk.STATE_NORMAL, gtk.gdk.color_parse('#FFFFFF'))
        vbox.pack_start(self._video_description, expand=False)

        self._lesson_text = gtk.Label()
        self._lesson_text.set_line_wrap(True)
        # self._lesson_text.modify_fg(
        #     gtk.STATE_NORMAL, gtk.gdk.color_parse('#FFFFFF'))
        self._lesson_text.set_size_request(
            gtk.gdk.screen_width() - style.GRID_CELL_SIZE * 3,
            gtk.gdk.screen_height() - style.GRID_CELL_SIZE * 3)
        vbox.pack_start(self._lesson_text, expand=True, fill=True, padding=10)
        vbox.show()

        # Try to fix description height to 4 lines so that it doesn't
        # shift size while changing videos.
        # self._video_description.set_text('\n\n\n\n')
        # size_req = self._video_description.size_request()
        # self._video_description.set_size_request(-1, size_req[1])

    def write_file(self, file_path):
        # Force video to stop
        self._stop_clicked(None)

    def _generate_menu(self):
        for child in self._menu.get_children():
            self._menu.remove(child)

        for (i, video) in enumerate(self._videos):
            print video[0]
            path = os.path.join(
                activity.get_bundle_path(), 'thumbnails',
                video[0][0:-4] + '.png')  # FIXME: make more robust
            button = VideoButton(video[1], path)
            button.connect('button_press_event', self.__menu_item_clicked, i)

            col = i % 4
            row = i / 4
            self._menu.attach(button, col, col + 1, row, row + 1)
            button.show_all()

    def _toggle_lesson(self, button):
        if not self._lesson_state:
            self._pause_clicked(None)
            self._show_lesson()
        else:
            self._hide_lesson()
            self._play_clicked(None)

    def _show_lesson(self): 
        self._lesson_state = True
        self._video.hide()
        self._video_description.hide()
        self._lesson_text.show()
        self._lesson_button.set_sensitive(False)

    def _hide_lesson(self): 
        self._lesson_state = False
        self._lesson_text.hide()
        self._video.show()
        self._video_description.show()
        self._lesson_button.set_sensitive(True)

    def _play_video(self, idx):
        video = self._videos[idx]
        self._menu.hide()
        self._hide_lesson()
        self._video.show()
        self._video.stop()

        self._video_title.set_markup('<span size="x-large" weight="bold">' + \
                                         glib.markup_escape_text(video[1]) + \
                                         '</span>')
        self._video_title.show()

        if len(video) > 2:
            self._video_description.set_text(video[2].strip())
        else:
            self._video_description.set_text('')
        self._video_description.show()
        self._lesson_button.set_sensitive(True)

        self._lesson_text.set_markup(self._lessons[idx])

        self._video.play(video[0])
        self._current_video_idx = idx

    def __menu_item_clicked(self, widget, event, idx):
        self._pause_button.set_sensitive(True)
        self._stop_button.set_sensitive(True)
        self._lesson_button.set_sensitive(True)
        self._play_button.set_sensitive(False)
        self._play_video(idx)

    def _play_clicked(self, widget):
        if self._lesson_state:
            self._hide_lesson()
        if self._video.paused:
            self._video.unpause()
        else:
            self._play_video(self._current_video_idx)
        self._pause_button.set_sensitive(True)
        self._stop_button.set_sensitive(True)
        self._lesson_button.set_sensitive(True)
        self._play_button.set_sensitive(False)

    def _pause_clicked(self, widget):
        if not self._video.paused:
            self._video.pause()
            self._pause_button.set_sensitive(False)
            self._stop_button.set_sensitive(True)
            self._lesson_button.set_sensitive(True)
            self._play_button.set_sensitive(True)

    def _stop_clicked(self, widget):
        if self._video.playing:
            self._video.stop()
            self._pause_button.set_sensitive(False)
            self._stop_button.set_sensitive(False)
            self._lesson_button.set_sensitive(False)
            self._play_button.set_sensitive(True)
        self._video.hide()
        self._video_title.hide()
        self._video_description.hide()
        self._lesson_text.hide()
        self._menu.show()
