import unittest

from qctoolkit.pulses.measurement_pulse_template import MeasurementPulseTemplate
from tests.pulses.sequencing_dummies import DummyPulseTemplate, DummyWaveform


class MeasurementPulseTemplateTests(unittest.TestCase):

    def test_init(self) -> None:
        wf = DummyWaveform(num_channels=7)
        dummy = DummyPulseTemplate(requires_stop=True, is_interruptable=False, num_channels=7,
                                   parameter_names={'foo', 'bar'}, waveform=wf)
        template = MeasurementPulseTemplate(dummy, identifier='foo')
        self.assertEqual('foo', template.identifier)
        self.assertFalse(template.is_interruptable())
        self.assertEqual(dummy.requires_stop(dict(), dict()), template.requires_stop(dict(), dict()))
        self.assertEqual(dummy.num_channels, template.num_channels)
        self.assertEqual(dummy.parameter_names, template.parameter_names)
        self.assertEqual(dummy.parameter_declarations, template.parameter_declarations)
        self.assertEqual(dummy.build_waveform(dict()), template.build_waveform(dict()))
