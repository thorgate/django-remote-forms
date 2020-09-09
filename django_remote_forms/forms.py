from collections import OrderedDict

from django.forms import ModelForm

from django_remote_forms import fields, logger
from django_remote_forms.utils import resolve_promise


class RemoteForm(object):
    def __init__(self, form, *args, **kwargs):
        self.form = form

        self.all_fields = set(self.form.fields.keys())

        self.excluded_fields = set(kwargs.pop('exclude', []))
        self.included_fields = set(kwargs.pop('include', []))
        self.readonly_fields = set(kwargs.pop('readonly', []))
        self.ordered_fields = kwargs.pop('ordering', [])

        if isinstance(form, ModelForm):
            if hasattr(form, '_meta'):
                self.included_fields = self.included_fields or set(getattr(form._meta, 'fields') or [])
                self.excluded_fields = self.excluded_fields or set(getattr(form._meta, 'exclude') or [])

        self.fieldsets = kwargs.pop('fieldsets', {})

        # Make sure all passed field lists are valid
        if self.excluded_fields and not (self.all_fields >= self.excluded_fields):
            logger.warning('Excluded fields %s are not present in form fields' % (self.excluded_fields - self.all_fields))
            self.excluded_fields = set()

        if self.included_fields and not (self.all_fields >= self.included_fields):
            logger.warning('Included fields %s are not present in form fields' % (self.included_fields - self.all_fields))
            self.included_fields = set()

        if self.readonly_fields and not (self.all_fields >= self.readonly_fields):
            logger.warning('Readonly fields %s are not present in form fields' % (self.readonly_fields - self.all_fields))
            self.readonly_fields = set()

        if self.ordered_fields and not (self.all_fields >= set(self.ordered_fields)):
            logger.warning('Readonly fields %s are not present in form fields' % (set(self.ordered_fields) - self.all_fields))
            self.ordered_fields = []

        if self.included_fields | self.excluded_fields:
            logger.warning('Included and excluded fields have following fields %s in common' % (set(self.ordered_fields) - self.all_fields))
            self.excluded_fields = set()
            self.included_fields = set()

        # Extend exclude list from include list
        self.excluded_fields |= (self.included_fields - self.all_fields)

        if not self.ordered_fields:
            if hasattr(self.form.fields, 'keyOrder'):
                self.ordered_fields = self.form.fields.keyOrder
            else:
                self.ordered_fields = self.form.fields.keys()

        self.fields = []

        # Construct ordered field list considering exclusions
        for field_name in self.ordered_fields:
            if field_name in self.excluded_fields:
                continue

            self.fields.append(field_name)

        # Validate fieldset
        fieldset_fields = set()
        if self.fieldsets:
            for fieldset_name, fieldsets_data in self.fieldsets:
                if 'fields' in fieldsets_data:
                    fieldset_fields |= set(fieldsets_data['fields'])

        if not (self.all_fields >= fieldset_fields):
            logger.warning('Following fieldset fields are invalid %s' % (fieldset_fields - self.all_fields))
            self.fieldsets = {}

        if not (set(self.fields) >= fieldset_fields):
            logger.warning('Following fieldset fields are excluded %s' % (fieldset_fields - set(self.fields)))
            self.fieldsets = {}

    def as_dict(self):
        """
        Returns a form as a dictionary that looks like the following:

        form = {
            'non_field_errors': [],
            'label_suffix': ':',
            'is_bound': False,
            'prefix': 'text'.
            'fields': {
                'name': {
                    'type': 'type',
                    'errors': {},
                    'help_text': 'text',
                    'label': 'text',
                    'initial': 'data',
                    'max_length': 'number',
                    'min_length: 'number',
                    'required': False,
                    'bound_data': 'data'
                    'widget': {
                        'attr': 'value'
                    }
                }
            }
        }
        """
        form_dict = OrderedDict()
        form_dict['title'] = self.form.__class__.__name__
        form_dict['non_field_errors'] = self.form.non_field_errors()
        form_dict['label_suffix'] = self.form.label_suffix
        form_dict['is_bound'] = self.form.is_bound
        form_dict['prefix'] = self.form.prefix
        form_dict['fields'] = OrderedDict()
        form_dict['errors'] = self.form.errors
        form_dict['fieldsets'] = getattr(self.form, 'fieldsets', [])

        # If there are no fieldsets, specify order
        form_dict['ordered_fields'] = self.fields

        try:
            from crispy_forms.helper import FormHelper
            from crispy_forms.layout import Layout

            if hasattr(self.form, 'helper'):
                if isinstance(self.form.helper, FormHelper):
                    if self.form.helper.layout is not None:

                        if isinstance(self.form.helper.layout, Layout):
                            form_dict['layout'] = self.parse_layout(self.form.helper.layout)

        except ImportError:
            pass

        initial_data = {}

        for name, field in [(x, self.form.fields[x]) for x in self.fields]:
            # Retrieve the initial data from the form itself if it exists so
            # that we properly handle which initial data should be returned in
            # the dictionary.

            # Please refer to the Django Form API documentation for details on
            # why this is necessary:
            # https://docs.djangoproject.com/en/dev/ref/forms/api/#dynamic-initial-values
            form_initial_field_data = self.form.initial.get(name)

            # Instantiate the Remote Forms equivalent of the field if possible
            # in order to retrieve the field contents as a dictionary.
            remote_field_class_name = 'Remote%s' % field.__class__.__name__
            try:
                remote_field_class = getattr(fields, remote_field_class_name)
                remote_field = remote_field_class(field, form_initial_field_data, field_name=name)
            except Exception as e:
                logger.warning('Error serializing field %s: %s', remote_field_class_name, str(e))
                field_dict = {}
            else:
                field_dict = remote_field.as_dict()

            if name in self.readonly_fields:
                field_dict['readonly'] = True

            form_dict['fields'][name] = field_dict

            # Load the initial data, which is a conglomerate of form initial and field initial
            if 'initial' not in form_dict['fields'][name]:
                form_dict['fields'][name]['initial'] = None

            initial_data[name] = form_dict['fields'][name]['initial']

        if self.form.data:
            form_dict['data'] = self.form.data
        else:
            form_dict['data'] = initial_data

        return resolve_promise(form_dict)

    def parse_layout(self, item):
        obj = {
            'children': [],
        }
        obj.update(self.parse_layout_class(item))

        if hasattr(item, 'fields') and obj['type'] != 'field':
            for i, layout_object in enumerate(item.fields):
                obj['children'].append(self.parse_layout(layout_object))

        return obj

    def parse_layout_class(self, instance):
        from django.utils.text import slugify
        from crispy_forms.layout import Layout, Div, Field

        res = {
            'type': slugify(instance.__class__.__name__),
        }

        if isinstance(instance, Div):
            res['attrs'] = self.parse_flat_attrs(instance.flat_attrs)
            res['attrs']['class'] = instance.css_class

        elif isinstance(instance, Field):
            if len(instance.fields) > 1:
                raise NotImplementedError('We only support 1 field at a time in Field object')

            res['name'] = instance.fields[0]
            res['attrs'] = instance.attrs

        elif isinstance(instance, dict):
            res.update(instance)

        elif not isinstance(instance, Layout):
            raise NotImplementedError('Unknown layout object %s: %s' % (instance.__class__.__name__, instance))

        res['attrs'] = self.keys_case(res.get('attrs', {}))

        return res

    def keys_case(self, attrs):
        new_attrs = {}

        for key, val in attrs.items():
            new_attrs[key.replace('-', '_')] = val

        return new_attrs

    def parse_flat_attrs(self, attrs):
        import xml.etree.ElementTree as ET

        tree = ET.fromstring('<element %s />' % attrs)
        return tree.attrib
