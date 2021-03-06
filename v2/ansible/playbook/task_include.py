# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

# Make coding more python3-ish
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.errors import AnsibleParserError
from ansible.parsing.splitter import split_args, parse_kv
from ansible.parsing.yaml.objects import AnsibleBaseYAMLObject, AnsibleMapping
from ansible.playbook.attribute import Attribute, FieldAttribute
from ansible.playbook.base import Base
from ansible.playbook.conditional import Conditional
from ansible.playbook.helpers import load_list_of_blocks, compile_block_list
from ansible.playbook.taggable import Taggable
from ansible.plugins import lookup_loader


__all__ = ['TaskInclude']


class TaskInclude(Base, Conditional, Taggable):

    '''
    A class used to wrap the use of `include: /some/other/file.yml`
    within a task list, which may return a list of Task objects and/or
    more TaskInclude objects.
    '''

    # the description field is used mainly internally to
    # show a nice reprsentation of this class, rather than
    # simply using __class__.__name__

    __desc__ = "task include statement"


    #-----------------------------------------------------------------
    # Attributes

    _name      = FieldAttribute(isa='string')
    _include   = FieldAttribute(isa='string')
    _loop      = FieldAttribute(isa='string', private=True)
    _loop_args = FieldAttribute(isa='list', private=True)
    _tags      = FieldAttribute(isa='list', default=[])
    _vars      = FieldAttribute(isa='dict', default=dict())
    _when      = FieldAttribute(isa='list', default=[])

    def __init__(self, block=None, role=None, task_include=None, use_handlers=False):
        self._block        = block
        self._role         = role
        self._task_include = task_include
        self._use_handlers = use_handlers

        self._task_blocks  = []

        super(TaskInclude, self).__init__()

    @staticmethod
    def load(data, block=None, role=None, task_include=None, use_handlers=False, variable_manager=None, loader=None):
        ti = TaskInclude(block=block, role=role, task_include=None, use_handlers=use_handlers)
        return ti.load_data(data, variable_manager=variable_manager, loader=loader)

    def munge(self, ds):
        '''
        Regorganizes the data for a TaskInclude datastructure to line
        up with what we expect the proper attributes to be
        '''

        assert isinstance(ds, dict)

        # the new, cleaned datastructure, which will have legacy
        # items reduced to a standard structure
        new_ds = AnsibleMapping()
        if isinstance(ds, AnsibleBaseYAMLObject):
            new_ds.copy_position_info(ds)

        for (k,v) in ds.iteritems():
            if k == 'include':
                self._munge_include(ds, new_ds, k, v)
            elif k.replace("with_", "") in lookup_loader:
                self._munge_loop(ds, new_ds, k, v)
            else:
                # some basic error checking, to make sure vars are properly
                # formatted and do not conflict with k=v parameters
                # FIXME: we could merge these instead, but controlling the order
                #        in which they're encountered could be difficult
                if k == 'vars':
                    if 'vars' in new_ds:
                        raise AnsibleParserError("include parameters cannot be mixed with 'vars' entries for include statements", obj=ds)
                    elif not isinstance(v, dict):
                        raise AnsibleParserError("vars for include statements must be specified as a dictionary", obj=ds)
                new_ds[k] = v

        return new_ds

    def _munge_include(self, ds, new_ds, k, v):
        '''
        Splits the include line up into filename and parameters
        '''

        # The include line must include at least one item, which is the filename
        # to include. Anything after that should be regarded as a parameter to the include
        items = split_args(v)
        if len(items) == 0:
            raise AnsibleParserError("include statements must specify the file name to include", obj=ds)
        else:
            # FIXME/TODO: validate that items[0] is a file, which also
            #             exists and is readable 
            new_ds['include'] = items[0]
            if len(items) > 1:
                # rejoin the parameter portion of the arguments and
                # then use parse_kv() to get a dict of params back
                params = parse_kv(" ".join(items[1:]))
                if 'vars' in new_ds:
                    # FIXME: see fixme above regarding merging vars
                    raise AnsibleParserError("include parameters cannot be mixed with 'vars' entries for include statements", obj=ds)
                new_ds['vars'] = params

    def _munge_loop(self, ds, new_ds, k, v):
        ''' take a lookup plugin name and store it correctly '''

        loop_name = k.replace("with_", "")
        if new_ds.get('loop') is not None:
            raise AnsibleError("duplicate loop in task: %s" % loop_name)
        new_ds['loop'] = loop_name
        new_ds['loop_args'] = v


    def _load_include(self, attr, ds):
        ''' loads the file name specified in the ds and returns a list of blocks '''

        data = self._loader.load_from_file(ds)
        if not isinstance(data, list):
            raise AnsibleParsingError("included task files must contain a list of tasks", obj=ds)

        self._task_blocks = load_list_of_blocks(
            data,
            parent_block=self._block,
            task_include=self,
            role=self._role,
            use_handlers=self._use_handlers,
            loader=self._loader
        )
        return ds

    def compile(self):
        '''
        Returns the task list for the included tasks.
        '''

        task_list = []
        task_list.extend(compile_block_list(self._task_blocks))
        return task_list

    def get_vars(self):
        '''
        Returns the vars for this task include, but also first merges in
        those from any parent task include which may exist.
        '''

        all_vars = dict()
        if self._task_include:
            all_vars.update(self._task_include.get_vars())
        all_vars.update(self.vars)
        return all_vars

    def serialize(self):

        data = super(TaskInclude, self).serialize()

        if self._block:
            data['block'] = self._block.serialize()

        if self._role:
            data['role'] = self._role.serialize()

        if self._task_include:
            data['task_include'] = self._task_include.serialize()

        return data

    def deserialize(self, data):

        # import here to prevent circular importing issues
        from ansible.playbook.block import Block
        from ansible.playbook.role import Role

        block_data = data.get('block')
        if block_data:
            b = Block()
            b.deserialize(block_data)
            self._block = b
            del data['block']

        role_data = data.get('role')
        if role_data:
            r = Role()
            r.deserialize(role_data)
            self._role = r
            del data['role']

        ti_data = data.get('task_include')
        if ti_data:
            ti = TaskInclude()
            ti.deserialize(ti_data)
            self._task_include = ti
            del data['task_include']

        super(TaskInclude, self).deserialize(data)

    def evaluate_conditional(self, all_vars):
        if self._task_include is not None:
            if not self._task_include.evaluate_conditional(all_vars):
                return False
        if self._block is not None:
            if not self._block.evaluate_conditional(all_vars):
                return False
        elif self._role is not None:
            if not self._role.evaluate_conditional(all_vars):
                return False
        return super(TaskInclude, self).evaluate_conditional(all_vars)

    def set_loader(self, loader):
        self._loader = loader
        if self._block:
            self._block.set_loader(loader)
        elif self._task_include:
            self._task_include.set_loader(loader)
