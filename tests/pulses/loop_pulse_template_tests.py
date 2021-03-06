import unittest

from sympy import sympify

from qctoolkit.expressions import Expression
from qctoolkit.pulses.loop_pulse_template import ForLoopPulseTemplate, WhileLoopPulseTemplate,\
    ConditionMissingException, ParametrizedRange, LoopIndexNotUsedException, LoopPulseTemplate
from qctoolkit.pulses.parameters import ConstantParameter, InvalidParameterNameException, ParameterConstraintViolation
from qctoolkit.pulses.instructions import MEASInstruction

from tests.pulses.sequencing_dummies import DummyCondition, DummyPulseTemplate, DummySequencer, DummyInstructionBlock,\
    DummyParameter
from tests.serialization_dummies import DummySerializer


class DummyLoopPulseTemplate(LoopPulseTemplate):
    pass
DummyLoopPulseTemplate.__abstractmethods__ = set()


class LoopPulseTemplateTests(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def test_body(self):
        body = DummyPulseTemplate()
        tpl = DummyLoopPulseTemplate(body)
        self.assertIs(tpl.body, body)

    def test_defined_channels(self):
        body = DummyPulseTemplate(defined_channels={'A'})
        tpl = DummyLoopPulseTemplate(body)
        self.assertIs(tpl.defined_channels, body.defined_channels)

    def test_measurement_names(self):
        body = DummyPulseTemplate(measurement_names={'A'})
        tpl = DummyLoopPulseTemplate(body)
        self.assertIs(tpl.measurement_names, body.measurement_names)


class ParametrizedRangeTest(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def test_init(self):
        self.assertEqual(ParametrizedRange(7).to_tuple(),
                         (0, 7, 1))
        self.assertEqual(ParametrizedRange(4, 7).to_tuple(),
                         (4, 7, 1))
        self.assertEqual(ParametrizedRange(4, 'h', 5).to_tuple(),
                         (4, 'h', 5))

        self.assertEqual(ParametrizedRange(start=7, stop=1, step=-1).to_tuple(),
                         (7, 1, -1))

        with self.assertRaises(TypeError):
            ParametrizedRange()
        with self.assertRaises(TypeError):
            ParametrizedRange(1, 2, 3, 4)

        with self.assertRaises(TypeError):
            ParametrizedRange(1, 2, stop=6)

    def test_to_range(self):
        pr = ParametrizedRange(4, 'l*k', 'k')

        self.assertEqual(pr.to_range({'l': 5, 'k': 2}),
                         range(4, 10, 2))

    def test_parameter_names(self):
        self.assertEqual(ParametrizedRange(5).parameter_names, set())
        self.assertEqual(ParametrizedRange('g').parameter_names, {'g'})
        self.assertEqual(ParametrizedRange('g*h', 'h', 'l/m').parameter_names, {'g', 'h', 'l', 'm'})


class ForLoopPulseTemplateTest(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def test_init(self):
        dt = DummyPulseTemplate(parameter_names={'i', 'k'})
        self.assertEqual(ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=5).loop_range.to_tuple(),
                         (0, 5, 1))
        self.assertEqual(ForLoopPulseTemplate(body=dt, loop_index='i', loop_range='s').loop_range.to_tuple(),
                         (0, 's', 1))
        self.assertEqual(ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=(2, 5)).loop_range.to_tuple(),
                         (2, 5, 1))
        self.assertEqual(ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=range(1, 2, 5)).loop_range.to_tuple(),
                         (1, 2, 5))
        self.assertEqual(ForLoopPulseTemplate(body=dt, loop_index='i',
                                              loop_range=ParametrizedRange('a', 'b', 'c')).loop_range.to_tuple(),
                         ('a', 'b', 'c'))

        with self.assertRaises(InvalidParameterNameException):
            ForLoopPulseTemplate(body=dt, loop_index='1i', loop_range=6)

        with self.assertRaises(ValueError):
            ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=slice(None))

        with self.assertRaises(LoopIndexNotUsedException):
            ForLoopPulseTemplate(body=DummyPulseTemplate(), loop_index='i', loop_range=1)

    def test_body_parameter_generator(self):
        dt = DummyPulseTemplate(parameter_names={'i', 'k'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('a', 'b', 'c'))

        expected_range = range(2, 17, 3)

        outer_params = dict(k=ConstantParameter(5),
                            a=ConstantParameter(expected_range.start),
                            b=ConstantParameter(expected_range.stop),
                            c=ConstantParameter(expected_range.step))
        forward_parameter_dicts = list(flt._body_parameter_generator(outer_params, forward=True))
        backward_parameter_dicts = list(flt._body_parameter_generator(outer_params, forward=False))

        self.assertEqual(forward_parameter_dicts, list(reversed(backward_parameter_dicts)))
        for local_params, i in zip(forward_parameter_dicts, expected_range):
            expected_local_params = dict(k=ConstantParameter(5), i=ConstantParameter(i))
            self.assertEqual(expected_local_params, local_params)

    def test_loop_index(self):
        loop_index = 'i'
        dt = DummyPulseTemplate(parameter_names={'i', 'k'})
        flt = ForLoopPulseTemplate(body=dt, loop_index=loop_index, loop_range=('a', 'b', 'c'))
        self.assertIs(loop_index, flt.loop_index)

    def test_duration(self):
        dt = DummyPulseTemplate(parameter_names={'idx', 'd'}, duration=Expression('d+idx*2'))

        flt = ForLoopPulseTemplate(body=dt, loop_index='idx', loop_range='n')
        self.assertEqual(flt.duration.evaluate_numeric(n=4, d=100), 4 * 100 + 2 * (1 + 2 + 3))

        flt = ForLoopPulseTemplate(body=dt, loop_index='idx', loop_range=(3, 'n', 2))
        self.assertEqual(flt.duration.evaluate_numeric(n=9, d=100), 3*100 + 2*(3 + 5 + 7))
        self.assertEqual(flt.duration.evaluate_numeric(n=8, d=100), 3 * 100 + 2 * (3 + 5 + 7))

        flt = ForLoopPulseTemplate(body=dt, loop_index='idx', loop_range=('m', 'n', -2))
        self.assertEqual(flt.duration.evaluate_numeric(n=9, d=100, m=14),
                         3 * 100 + 2 * (14 + 12 + 10))

        flt = ForLoopPulseTemplate(body=dt, loop_index='idx', loop_range=('m', 'n', -2))
        self.assertEqual(flt.duration.evaluate_numeric(n=9, d=100, m=14),
                         3 * 100 + 2*(14 + 12 + 10))

    def test_parameter_names(self):
        dt = DummyPulseTemplate(parameter_names={'i', 'k'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('a', 'b', 'c'))

        self.assertEqual(flt.parameter_names, {'k', 'a', 'b', 'c'})

    def test_build_sequence(self):
        dt = DummyPulseTemplate(parameter_names={'i'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('a', 'b', 'c'),
                                   measurements=[('A', 0, 1)], parameter_constraints=['c > 1'])

        sequencer = DummySequencer()
        block = DummyInstructionBlock()
        invalid_parameters = {'a': ConstantParameter(1), 'b': ConstantParameter(4), 'c': ConstantParameter(1)}
        parameters = {'a': ConstantParameter(1), 'b': ConstantParameter(4), 'c': ConstantParameter(2)}
        measurement_mapping = dict(A='B')
        channel_mapping = dict(C='D')

        with self.assertRaises(ParameterConstraintViolation):
            flt.build_sequence(sequencer, invalid_parameters, dict(), measurement_mapping, channel_mapping, block)

        self.assertEqual(block.instructions, [])
        self.assertNotIn(block, sequencer.sequencing_stacks)

        flt.build_sequence(sequencer, parameters, dict(), measurement_mapping, channel_mapping, block)

        self.assertEqual(block.instructions, [MEASInstruction(measurements=[('B', 0, 1)])])

        expected_stack = [(dt, {'i': ConstantParameter(3)}, dict(), measurement_mapping, channel_mapping),
                          (dt, {'i': ConstantParameter(1)}, dict(), measurement_mapping, channel_mapping)]

        self.assertEqual(sequencer.sequencing_stacks[block], expected_stack)

    def test_requires_stop(self):
        parameters = dict(A=DummyParameter(requires_stop=False), B=DummyParameter(requires_stop=False))

        dt = DummyPulseTemplate(parameter_names={'i'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('A', 'B'))

        self.assertFalse(flt.requires_stop(parameters, dict()))

        parameters['A'] = DummyParameter(requires_stop=True)
        self.assertTrue(flt.requires_stop(parameters, dict()))

    def test_get_serialization_data_minimal(self):

        dt = DummyPulseTemplate(parameter_names={'i'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('A', 'B'))

        def check_dt(to_dictify) -> str:
            self.assertIs(to_dictify, dt)
            return 'dt'

        serializer = DummySerializer(serialize_callback=check_dt)

        data = flt.get_serialization_data(serializer)
        expected_data = dict(body='dt',
                             loop_range=('A', 'B', 1),
                             loop_index='i')
        self.assertEqual(data, expected_data)

    def test_get_serialization_data_all_features(self):
        measurements = [('a', 0, 1), ('b', 1, 1)]
        parameter_constraints = ['foo < 3']

        dt = DummyPulseTemplate(parameter_names={'i'})
        flt = ForLoopPulseTemplate(body=dt, loop_index='i', loop_range=('A', 'B'),
                                   measurements=measurements, parameter_constraints=parameter_constraints)

        def check_dt(to_dictify) -> str:
            self.assertIs(to_dictify, dt)
            return 'dt'

        serializer = DummySerializer(serialize_callback=check_dt)

        data = flt.get_serialization_data(serializer)
        expected_data = dict(body='dt',
                             loop_range=('A', 'B', 1),
                             loop_index='i',
                             measurements=measurements,
                             parameter_constraints=parameter_constraints)
        self.assertEqual(data, expected_data)

    def test_deserialize_minimal(self):
        body_str = 'dt'
        dt = DummyPulseTemplate(parameter_names={'i'})

        def make_dt(ident: str):
            self.assertEqual(body_str, ident)
            return ident

        data = dict(body=body_str,
                    loop_range=('A', 'B', 1),
                    loop_index='i',
                    identifier='meh')

        serializer = DummySerializer(deserialize_callback=make_dt)
        serializer.subelements['dt'] = dt

        flt = ForLoopPulseTemplate.deserialize(serializer, **data)
        self.assertEqual(flt.identifier, 'meh')
        self.assertEqual(flt.body, dt)
        self.assertEqual(flt.loop_index, 'i')
        self.assertEqual(flt.loop_range.to_tuple(), ('A', 'B', 1))

    def test_deserialize_all_features(self):
        body_str = 'dt'
        dt = DummyPulseTemplate(parameter_names={'i'})

        measurements = [('a', 0, 1), ('b', 1, 1)]
        parameter_constraints = ['foo < 3']

        def make_dt(ident: str):
            self.assertEqual(body_str, ident)
            return ident

        data = dict(body=body_str,
                    loop_range=('A', 'B', 1),
                    loop_index='i',
                    identifier='meh',
                    measurements=measurements,
                    parameter_constraints=parameter_constraints)

        serializer = DummySerializer(deserialize_callback=make_dt)
        serializer.subelements['dt'] = dt

        flt = ForLoopPulseTemplate.deserialize(serializer, **data)
        self.assertEqual(flt.identifier, 'meh')
        self.assertIs(flt.body, dt)
        self.assertEqual(flt.loop_index, 'i')
        self.assertEqual(flt.loop_range.to_tuple(), ('A', 'B', 1))
        self.assertEqual(flt.measurement_declarations, measurements)
        self.assertEqual([str(c) for c in flt.parameter_constraints], parameter_constraints)




class WhileLoopPulseTemplateTest(unittest.TestCase):

    def test_parameter_names_and_declarations(self) -> None:
        condition = DummyCondition()
        body = DummyPulseTemplate()
        t = WhileLoopPulseTemplate(condition, body)
        self.assertEqual(body.parameter_names, t.parameter_names)

        body.parameter_names_ = {'foo', 't', 'bar'}
        self.assertEqual(body.parameter_names, t.parameter_names)

    @unittest.skip
    def test_is_interruptable(self) -> None:
        condition = DummyCondition()
        body = DummyPulseTemplate(is_interruptable=False)
        t = WhileLoopPulseTemplate(condition, body)
        self.assertFalse(t.is_interruptable)

        body.is_interruptable_ = True
        self.assertTrue(t.is_interruptable)

    def test_str(self) -> None:
        condition = DummyCondition()
        body = DummyPulseTemplate()
        t = WhileLoopPulseTemplate(condition, body)
        self.assertIsInstance(str(t), str)


class LoopPulseTemplateSequencingTests(unittest.TestCase):

    def test_requires_stop(self) -> None:
        condition = DummyCondition(requires_stop=False)
        conditions = {'foo_cond': condition}
        body = DummyPulseTemplate(requires_stop=False)
        t = WhileLoopPulseTemplate('foo_cond', body)
        self.assertFalse(t.requires_stop({}, conditions))

        condition.requires_stop_ = True
        self.assertTrue(t.requires_stop({}, conditions))

        body.requires_stop_ = True
        condition.requires_stop_ = False
        self.assertFalse(t.requires_stop({}, conditions))

    def test_build_sequence(self) -> None:
        condition = DummyCondition()
        body = DummyPulseTemplate()
        t = WhileLoopPulseTemplate('foo_cond', body)
        sequencer = DummySequencer()
        block = DummyInstructionBlock()
        parameters = {}
        conditions = {'foo_cond': condition}
        measurement_mapping = {'swag': 'aufdrehen'}
        channel_mapping = {}
        t.build_sequence(sequencer, parameters, conditions, measurement_mapping, channel_mapping, block)
        expected_data = dict(
            delegator=t,
            body=body,
            sequencer=sequencer,
            parameters=parameters,
            conditions=conditions,
            measurement_mapping=measurement_mapping,
            channel_mapping=channel_mapping,
            instruction_block=block
        )
        self.assertEqual(expected_data, condition.loop_call_data)
        self.assertFalse(condition.branch_call_data)
        self.assertFalse(sequencer.sequencing_stacks)

    def test_condition_missing(self) -> None:
        body = DummyPulseTemplate(requires_stop=False)
        t = WhileLoopPulseTemplate('foo_cond', body)
        sequencer = DummySequencer()
        block = DummyInstructionBlock()
        with self.assertRaises(ConditionMissingException):
            t.requires_stop({}, {})
            t.build_sequence(sequencer, {}, {}, {}, block)


class LoopPulseTemplateSerializationTests(unittest.TestCase):

    def test_get_serialization_data(self) -> None:
        body = DummyPulseTemplate()
        condition_name = 'foo_cond'
        identifier = 'foo_loop'
        t = WhileLoopPulseTemplate(condition_name, body, identifier=identifier)

        serializer = DummySerializer()
        expected_data = dict(type=serializer.get_type_identifier(t),
                             body=str(id(body)),
                             condition=condition_name)

        data = t.get_serialization_data(serializer)
        self.assertEqual(expected_data, data)

    def test_deserialize(self) -> None:
        data = dict(
            identifier='foo_loop',
            condition='foo_cond',
            body='bodyDummyPulse'
        )

        # prepare dependencies for deserialization
        serializer = DummySerializer()
        serializer.subelements[data['body']] = DummyPulseTemplate()

        # deserialize
        result = WhileLoopPulseTemplate.deserialize(serializer, **data)

        # compare
        self.assertIs(serializer.subelements[data['body']], result.body)
        self.assertEqual(data['condition'], result.condition)
        self.assertEqual(data['identifier'], result.identifier)


class ConditionMissingExceptionTest(unittest.TestCase):

    def test(self) -> None:
        exc = ConditionMissingException('foo')
        self.assertIsInstance(str(exc), str)


class LoopIndexNotUsedExceptionTest(unittest.TestCase):
    def str_test(self):
        self.assertEqual(str(LoopIndexNotUsedException('a', {'b', 'c'})), "The parameter a is missing in the body's parameter names: {}".format({'b', 'c'}))


if __name__ == "__main__":
    unittest.main(verbosity=2)