import unittest

import numpy

from qctoolkit.pulses.measurement_pulse_template import MeasurementPulseTemplate, MeasurementWaveform
from tests.pulses.sequencing_dummies import DummyPulseTemplate, DummyWaveform


class MeasurementPulseTemplateTests(unittest.TestCase):

    def test_init(self) -> None:
        numpy.random.seed(3637)
        wf = DummyWaveform(num_channels=7, sample_output=numpy.random.rand(11))
        dummy = DummyPulseTemplate(requires_stop=True, is_interruptable=False, num_channels=7,
                                   parameter_names={'foo', 'bar'}, waveform=wf)
        template = MeasurementPulseTemplate(dummy, 'raw', identifier='foo')
        self.assertEqual('foo', template.identifier)
        self.assertEqual('raw', template.measurement_type)
        self.assertFalse(template.is_interruptable())
        self.assertEqual(dummy.requires_stop(dict(), dict()), template.requires_stop(dict(), dict()))
        self.assertEqual(dummy.num_channels, template.num_channels)
        self.assertEqual(dummy.parameter_names, template.parameter_names)
        self.assertEqual(dummy.parameter_declarations, template.parameter_declarations)

        sample_times = numpy.linspace(0, 10, 11)
        self.assertEqual(list(dummy.build_waveform(dict()).sample(sample_times, 16.3)),
                         list(template.build_waveform(dict()).sample(sample_times, 16.3)))


class MeasurementWaveformTests(unittest.TestCase):

    def test_init(self) -> None:
        numpy.random.seed(38364)
        dummy = DummyWaveform(duration=3.3, sample_output=numpy.random.rand(11), num_channels=3)
        wf = MeasurementWaveform(dummy, 'raw')
        self.assertEqual('raw', wf.measurement_type)
        self.assertEqual(dummy.num_channels, wf.num_channels)
        self.assertEqual(dummy.duration, wf.duration)
        sample_times = numpy.linspace(0, 10, 11)
        self.assertEqual(list(dummy.sample(sample_times, 16.3)), list(wf.sample(sample_times, 16.3)))
