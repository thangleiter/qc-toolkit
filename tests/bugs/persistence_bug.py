# coding: utf-8
import unittest
import numpy as np
import qctoolkit.pulses as pls
from qctoolkit.pulses.instructions import EXECInstruction as EXEC

class PersistenceBug(unittest.TestCase):
    def test_measurement_persistence_bug(self):
        p = pls.TablePulseTemplate(measurement=True, identifier='measure')
        p.add_entry(1,3, 'linear')
        p.add_entry(2,5, 'hold')
        p.add_entry(3,0,'jump')

        s = pls.Sequencer()
        s.push(p)
        program = s.build()
        ex = program[0]
        windows = ex.waveform.measurement_windows

        s2 = pls.Sequencer()
        s2.push(p)
        program2 = s2.build()
        ex2 = program[0]
        windows2 = ex2.waveform.measurement_windows

        s3 = pls.Sequencer()
        s3.push(p)
        program3 = s3.build()
        ex3 = program[0]
        windows3 = ex3.waveform.measurement_windows

        self.assertEqual(windows, windows2)
        self.assertEqual(windows, windows3)


    def test_persistence_bugs_2(self):
        measure = pls.TablePulseTemplate(measurement=True, identifier='measurement')
        measure.add_entry(2,0)

        pulse = pls.TablePulseTemplate(identifier='pulse')
        pulse.add_entry('length', 100)

        seq = pls.SequencePulseTemplate([(measure, dict()),
                                        (pulse, {'length':'length - 10'})],
                                        ['length'])

        sequencer = pls.sequencing.Sequencer()

        parameters = np.linspace(50,100, 5)
        for p in parameters:
            sequencer.push(seq, parameters={'length':p})

        program = sequencer.build()

        def uniquify(inputlist):
            keys = list(set(inputlist))
            replacements = {k: v for k,v in zip(keys, range(len(keys)))}
            result = []
            for el in inputlist:
                result.append(replacements[el])
            return result

        def cumsum(it):
            total = 0
            yield total
            for x in it:
                total += x
                yield total

        blocks = list(filter(lambda x: type(x) == EXEC, program))
        wfs = [block.waveform for block in blocks]
        wfids = uniquify(list(map(id, wfs)))
        durations = [wf.duration for wf in wfs]
        windows = [wf.measurement_windows for wf in wfs]
        windowids = uniquify(list(map(id, windows)))

        cumulative = cumsum(durations)

        expected = [0, 2, 92.0, 94.0, 171.5, 173.5, 238.5, 240.5, 293.0, 295.0, 335.0]
        expected_windows = [[(0, 2)], [], [(0, 2)], [], [(0, 2)], [], [(0, 2)], [], [(0, 2)], []]
        self.assertEqual(expected, list(cumulative))
        self.assertEqual(windows, expected_windows)
