# Copyright (C) 2006-2011, University of Maryland
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/ or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# Author: James Krycka, Nikunj Patel

"""
This module implements the AppPanel class which creates the main panel on top
of the frame of the GUI for the Refl1D application.
"""

#==============================================================================

from __future__ import division
import os
import sys
import traceback

import wx
import wx.aui

from refl1d.profileview.panel import ProfileView
from refl1d.profileview.theory import TheoryView
from refl1d.cli import load_problem

from .. import fitters
from .summary_view import SummaryView
from .parameter_view import ParameterView
from .log_view import LogView
from .fit_dialog import OpenFitOptions
from .fit_thread import (FitThread, EVT_FIT_PROGRESS,
                         EVT_FIT_IMPROVEMENT, EVT_FIT_COMPLETE)
from .util import nice
from . import signal
from .utilities import get_bitmap

# File selection strings.
MODEL_FILES = "Model files (*.r1d)|*.r1d"
PYTHON_FILES = "Script files (*.py)|*.py"
REFL_FILES = "Refl files (*.refl)|*.refl"
DATA_FILES = "Data files (*.dat)|*.dat"
TEXT_FILES = "Text files (*.txt)|*.txt"
ALL_FILES = "All files (*.*)|*"

# Custom colors.
WINDOW_BKGD_COLOUR = "#ECE9D8"

#==============================================================================

class AppPanel(wx.Panel):
    """
    This class builds the GUI for the application on a panel and attaches it
    to the frame.
    """

    def __init__(self, *args, **kw):
        # Create a panel on the frame.  This will be the only child panel of
        # the frame and it inherits its size from the frame which is useful
        # during resize operations (as it provides a minimal size to sizers).

        wx.Panel.__init__(self, *args, **kw)

        self.SetBackgroundColour("WHITE")

        # Modify the tool bar.
        frame = self.GetTopLevelParent()
        self.init_toolbar(frame)
        self.init_menubar(frame)

        # Reconfigure the status bar.
        self.init_statusbar(frame, [-34, -50, -16, -16])

        # Create the model views
        self.init_views()

        # Add data menu
        mb = frame.GetMenuBar()
        data_view = self.view['data']
        if hasattr(data_view, 'menu'):
            mb.Append(data_view.menu(), data_view.title)

        # Create a PubSub receiver.
        signal.connect(self.OnLogMessage, "log")
        signal.connect(self.OnModelNew, "model.new")
        signal.connect(self.OnModelChange, "model.update_structure")
        signal.connect(self.OnModelSetpar, "model.update_parameters")

        EVT_FIT_PROGRESS(self, self.OnFitProgress)
        EVT_FIT_IMPROVEMENT(self, self.OnFitImprovement)
        EVT_FIT_COMPLETE(self, self.OnFitComplete)
        self.fit_thread = None

    def init_menubar(self, frame):
        """
        Adds items to the menu bar, menus, and menu options.
        The menu bar should already have a simple File menu and a Help menu.
        """
        mb = frame.GetMenuBar()

        file_menu_id = mb.FindMenu("File")
        file_menu = mb.GetMenu(file_menu_id)
        help_menu = mb.GetMenu(mb.FindMenu("Help"))

        # Add items to the "File" menu (prepending them in reverse order).
        # Grey out items that are not currently implemented.
        file_menu.PrependSeparator()

        _item = file_menu.Prepend(wx.ID_ANY,
                                  "&Import",
                                  "Import script to define model")
        frame.Bind(wx.EVT_MENU, self.OnFileImport, _item)

        file_menu.PrependSeparator()

        _item = file_menu.Prepend(wx.ID_SAVEAS,
                                  "Save&As",
                                  "Save model as another name")
        frame.Bind(wx.EVT_MENU, self.OnFileSaveAs, _item)
        #file_menu.Enable(id=wx.ID_SAVEAS, enable=False)
        _item = file_menu.Prepend(wx.ID_SAVE,
                                  "&Save",
                                  "Save model")
        frame.Bind(wx.EVT_MENU, self.OnFileSave, _item)
        #file_menu.Enable(id=wx.ID_SAVE, enable=False)
        _item = file_menu.Prepend(wx.ID_OPEN,
                                  "&Open",
                                  "Open existing model")
        frame.Bind(wx.EVT_MENU, self.OnFileOpen, _item)
        #file_menu.Enable(id=wx.ID_OPEN, enable=False)
        _item = file_menu.Prepend(wx.ID_NEW,
                                  "&New",
                                  "Create new model")
        frame.Bind(wx.EVT_MENU, self.OnFileNew, _item)
        #file_menu.Enable(id=wx.ID_NEW, enable=False)


        # Add 'Fitting' menu to the menu bar and define its options.
        # Items are initially greyed out, but will be enabled after a script
        # is loaded.
        fit_menu = self.fit_menu = wx.Menu()

        _item = fit_menu.Append(wx.ID_ANY,
                                "&Start Fit",
                                "Start fitting operation")
        frame.Bind(wx.EVT_MENU, self.OnFitStart, _item)
        fit_menu.Enable(id=_item.GetId(), enable=False)
        self.fit_menu_start = _item

        _item = fit_menu.Append(wx.ID_ANY,
                                "&Stop Fit",
                                "Stop fitting operation")
        frame.Bind(wx.EVT_MENU, self.OnFitStop, _item)
        fit_menu.Enable(id=_item.GetId(), enable=False)
        self.fit_menu_stop = _item

        _item = fit_menu.Append(wx.ID_ANY,
                                "Fit &Options ...",
                                "Edit fitting options")
        frame.Bind(wx.EVT_MENU, self.OnFitOptions, _item)
        fit_menu.Enable(id=_item.GetId(), enable=False)
        self.fit_menu_options = _item

        mb.Append(fit_menu, "&Fitting")


    def init_toolbar(self, frame):
        """Populates the tool bar."""
        tb = self.tb = frame.GetToolBar()

        script_bmp = get_bitmap("import_script.png", wx.BITMAP_TYPE_PNG)
        start_bmp = get_bitmap("start_fit.png", wx.BITMAP_TYPE_PNG)
        stop_bmp = get_bitmap("stop_fit.png", wx.BITMAP_TYPE_PNG)

        _tool = tb.AddSimpleTool(wx.ID_ANY, script_bmp,
                                 "Import Script",
                                 "Load model from script")
        frame.Bind(wx.EVT_TOOL, self.OnFileImport, _tool)

        tb.AddSeparator()

        _tool = tb.AddSimpleTool(wx.ID_ANY, start_bmp,
                                 "Start Fit",
                                 "Start fitting operation")
        frame.Bind(wx.EVT_TOOL, self.OnFitStart, _tool)
        tb.EnableTool(_tool.GetId(), False)
        self.tb_start = _tool

        _tool = tb.AddSimpleTool(wx.ID_ANY, stop_bmp,
                                 "Stop Fit",
                                 "Stop fitting operation")
        frame.Bind(wx.EVT_TOOL, self.OnFitStop, _tool)
        tb.EnableTool(_tool.GetId(), False)
        self.tb_stop = _tool

        tb.Realize()
        frame.SetToolBar(tb)

    def init_statusbar(self, frame, subbars):
        """Divides the status bar into multiple segments."""

        self.sb = frame.GetStatusBar()
        self.sb.SetFieldsCount(len(subbars))
        self.sb.SetStatusWidths(subbars)

    def init_views(self):
        # initial view
        self.aui = wx.aui.AuiNotebook(self)
        self.aui.Bind(wx.aui.EVT_AUINOTEBOOK_PAGE_CLOSE, self.OnViewTabClose)
        self.view_constructor = {
            'data': TheoryView,
            'model': ProfileView,
            'parameter': ParameterView,
            'summary': SummaryView,
            'log': LogView,
            }
        self.view_list = ['data','model','parameter','summary','log']
        self.view = {}
        for v in self.view_list:
            self.view[v] = self.view_constructor[v](self.aui,
                                                    size=(600,600))
            self.aui.AddPage(self.view[v],self.view_constructor[v].title)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.aui, 1, wx.EXPAND)
        self.SetSizer(sizer)
        # Move this to gui_app.after_show since the sizing doesn't work
        # right until the frame is rendered.
        #self.aui.Split(0, wx.TOP)

    def OnViewTabClose(self, evt):
        win = self.aui.GetPage(evt.selection)
        #print "Closing tab",win.GetId()
        for k,w in self.view.items():
            if w == win:
                tag = k
                break
        else:
            raise RuntimeError("Lost track of view")
        #print "creating external frame"
        constructor = self.view_constructor[tag]
        frame = wx.Frame(self, title=constructor.title,
                         size=constructor.default_size)
        panel = constructor(frame)
        self.view[tag] = panel
        if hasattr(constructor, 'set_model'):
            panel.set_model(self.model)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(panel, 1, wx.EXPAND)
        frame.SetSizer(sizer)
        frame.Bind(wx.EVT_CLOSE, self.OnViewFrameClose)
        frame.Show()


    def OnViewFrameClose(self, evt):
        win = evt.GetEventObject()
        #print "Closing frame",win.GetId()
        for k,w in self.view.items():
            if w.GetParent() == win:
                tag = k
                break
        else:
            raise RuntimeError("Lost track of view!")
        constructor = self.view_constructor[tag]
        self.view[tag] = constructor(self.aui)
        self.aui.AddPage(self.view[tag],constructor.title)
        if hasattr(constructor, 'set_model'):
            self.view[tag].set_model(self.model)
        evt.Skip()

    # model viewer interface
    def OnLogMessage(self, message):
        for v in self.view.values():
            if hasattr(v, 'log_message'):
                v.log_message(message)

    def OnModelNew(self, model):
        self.set_model(model)

    def OnModelChange(self, model):
        for v in self.view.values():
            if hasattr(v, 'update_model'):
                v.update_model(model)

    def OnModelSetpar(self, model):
        for k,v in self.view.items():
            if hasattr(v, 'update_parameters'):
                #print "updating",k
                v.update_parameters(model)

    def OnFileNew(self, event):
        self.new_model()

    def OnFileOpen(self, event):
        # Load the script which will contain model definition and data.
        dlg = wx.FileDialog(self,
                            message="Select File",
                            #defaultDir=os.getcwd(),
                            #defaultFile="",
                            wildcard=(MODEL_FILES+"|"+ALL_FILES),
                            style=wx.OPEN|wx.CHANGE_DIR)

        # Wait for user to close the dialog.
        status = dlg.ShowModal()
        path = dlg.GetPath()
        dlg.Destroy()

        # Process file if user clicked okay.
        if status == wx.ID_OK:
            self.load_model(path)

    def OnFileSave(self, event):
        if self.model is not None and hasattr(self.model,'modelfile'):
            self.save_model()
        else:
            self.OnFileSaveAs(event)

    def OnFileSaveAs(self, event):
        dlg = wx.FileDialog(self,
                            message="Select File",
                            defaultDir=os.getcwd(),
                            defaultFile="",
                            wildcard=(MODEL_FILES+"|"+ALL_FILES),
                            style=wx.SAVE|wx.CHANGE_DIR|wx.OVERWRITE_PROMPT)
        # Wait for user to close the dialog.
        status = dlg.ShowModal()
        path = dlg.GetPath()
        dlg.Destroy()

        # Process file if user clicked okay.
        if status == wx.ID_OK:
            ## Need to check for overwrite before adding extension
            #if os.path.basename(path) == path:
            #    path += ".r1d"
            self.model.modelfile = path
            self.save_model()

    def OnFileImport(self, event):
        # Load the script which will contain model defination and data.
        dlg = wx.FileDialog(self,
                            message="Select Script File",
                            #defaultDir=os.getcwd(),
                            #defaultFile="",
                            wildcard=(PYTHON_FILES+"|"+ALL_FILES),
                            style=wx.OPEN|wx.CHANGE_DIR)

        # Wait for user to close the dialog.
        status = dlg.ShowModal()
        path = dlg.GetPath()
        dlg.Destroy()

        # Process file if user clicked okay.
        if status == wx.ID_OK:
            self.import_model(path)

    def OnFitOptions(self, event):
        OpenFitOptions()

    def OnFitStart(self, event):
        if self.fit_thread:
            self.sb.SetStatusText("Error: Fit already running")
            return
        try:
            if len(self.model.parameters) == 0:
                raise ValueError ("Problem has no fittable parameters")
        except ValueError:
            import traceback
            error_txt=traceback.format_exc()
            self.sb.SetStatusText("Error: No fittable parameters", 3)
            signal.log_message(message=error_txt)
            return

        # Start a new thread worker and give fit problem to the worker.
        fitopts = fitters.FIT_OPTIONS[fitters.FIT_DEFAULT]
        self.fit_thread = FitThread(win=self, problem=self.model,
                                    fitter=fitopts.fitter,
                                    options=fitopts.options)
        self.sb.SetStatusText("Fit status: Running", 3)

    def OnFitStop(self, event):
        print "Clicked on stop fit ..." # not implemented

    def OnFitComplete(self, event):
        self.fit_thread = None
        chisq = nice(2*event.value/event.problem.dof)
        signal.log_message(message="done with chisq %g"%chisq)
        event.problem.setp(event.point)
        signal.update_parameters(model=event.problem)
        #self.remember_best(self.fitter, event.problem)

        self.sb.SetStatusText("Fit status: Complete", 3)

    def OnFitProgress(self, event):
        chisq = nice(2*event.value/event.problem.dof)
        message = "step %5d chisq %g"%(event.step, chisq)
        signal.log_message(message=message)

    def OnFitImprovement(self, event):
        event.problem.setp(event.point)
        event.problem.model_update()
        signal.update_parameters(model=event.problem)

    def remember_best(self, fitter, problem, best):
        fitter.save(problem.output)

        try:
            problem.save(problem.output, best)
        except:
            pass
        sys.stdout = open(problem.output+".out", "w")

        self.pan1.Layout()

    def new_model(self):
        from ..fitplugin import new_model as gen
        self.set_model(gen())

    def load_model(self, path):
        try:
            import cPickle as serialize
            problem = serialize.load(open(path, 'rb'))
            problem.modelfile = path
            signal.model_new(model=problem)
        except:
            signal.log_message(message=traceback.format_exc())

    def import_model(self, path):
        try:
            problem = load_problem([path])
            signal.model_new(model=problem)
        except:
            signal.log_message(message=traceback.format_exc())

    def save_model(self):
        import cPickle as serialize
        serialize.dump(self.model, open(self.model.modelfile,'wb'))

    def _add_measurement_type(self, type):
        """
        Add the panels needed to view a measurement of the given type.

        *type* is fitness.__class__, where fitness is the measurement cost function.
        """
        raise NotImplementedError
        name = type.__name__
        if type not in self.data_tabs:
            constructor = getattr(p, 'data_panel', PlotPanel)
            tab = self.data_notebook.add_tab(type, name+" Data")
            constructor(tab)
        if type not in self.model_notebook:
            constructor = getattr(p, 'model_panel', PlotPanel)
            tab = self.model_notebook.add_tab(type, name+" Model")
            constructor(tab)

    def _view_problem(self, problem):
        """
        Set the model and data views to those necessary to display the problem.
        """
        raise NotImplementedError
        # What types of measurements do we have?
        types = set(p.fitness.__class__ for p in problem)
        for p in types: _add_measurement_type(p)

        # Show only the relevant views
        for p,tab in self.data_notebook.tabs():
            tab.Show(p in types)
        for p,tab in self.model_notebook.tabs():
            tab.Show(p in types)

    def set_model(self, model):
        # Inform the various tabs that the model they are viewing has changed.
        self.model = model

        # Point all of our views at the new model
        for v in self.view.values():
            if hasattr(v,'set_model'):
                v.set_model(model)
        self.console['model'] = model

        # Enable appropriate menu items.
        self.fit_menu.Enable(id=self.fit_menu_start.GetId(), enable=True)
        #self.fit_menu.Enable(id=self.fit_menu_stop.GetId(), enable=True)
        self.fit_menu.Enable(id=self.fit_menu_options.GetId(), enable=True)

        # Enable appropriate toolbar items.
        self.tb.EnableTool(id=self.tb_start.GetId(), enable=True)
        #self.tb.EnableTool(id=self.tb_stop.GetId(), enable=True)
