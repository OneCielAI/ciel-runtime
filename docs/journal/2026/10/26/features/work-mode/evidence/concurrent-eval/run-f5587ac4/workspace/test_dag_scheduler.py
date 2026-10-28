import unittest

from dag_scheduler import Scheduler, CycleError, StateError


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.s = Scheduler()

    def test_higher_priority_insertion_ties_not_lexical(self):
        s = self.s
        s.add('z', priority=2)
        s.add('a', priority=2)
        s.add('high', priority=100)
        s.add('low', priority=-5)
        self.assertEqual(s.ready(), ['high', 'z', 'a', 'low'])
        s.replace_deps('z', [])
        self.assertEqual(s.ready(), ['high', 'z', 'a', 'low'])

    def test_dependency_flow(self):
        s = self.s
        s.add('root')
        s.add('left', ['root'])
        s.add('right', ['root'], priority=1)
        s.add('join', ['left', 'right'])
        self.assertEqual(s.ready(), ['root'])
        with self.assertRaises(StateError):
            s.start('left')
        s.start('root')
        self.assertEqual(s.status('root'), 'running')
        self.assertEqual(s.ready(), [])
        s.finish('root')
        self.assertEqual(s.status('root'), 'done')
        self.assertEqual(s.ready(), ['right', 'left'])
        s.start('left')
        s.finish('left')
        self.assertNotIn('join', s.ready())
        s.start('right')
        s.finish('right')
        self.assertEqual(s.ready(), ['join'])

    def test_failed_dependency_blocks_descendants_and_retry(self):
        s = self.s
        s.add('root')
        s.add('child', ['root'])
        s.add('grandchild', ['child'])
        s.start('root')
        s.finish('root', success=False)
        self.assertEqual(s.status('root'), 'failed')
        self.assertEqual(s.status('child'), 'pending')
        self.assertEqual(s.ready(), [])
        s.retry('root')
        self.assertEqual(s.ready(), ['root'])
        self.assertEqual(s.status('root'), 'pending')
        s.start('root')
        self.assertNotIn('child', s.ready())
        s.finish('root')
        self.assertEqual(s.ready(), ['child'])
        s.start('child')
        s.finish('child')
        self.assertEqual(s.ready(), ['grandchild'])

    def test_replace_deps_success(self):
        s = self.s
        s.add('a')
        s.add('b')
        s.add('c', ['a'])
        s.replace_deps('c', (dep for dep in ['b', 'b']))
        s.start('b')
        s.finish('b')
        self.assertEqual(s.ready(), ['a', 'c'])

    def test_replace_deps_failure_is_atomic(self):
        s = self.s
        s.add('z')
        s.add('a', ['z'])
        s.add('c', ['a'])
        s.add('free')
        for deps, error in [(['unknown'], ValueError), (['a'], CycleError),
                            (['c'], CycleError), (['free', 'c'], CycleError)]:
            before = (s.ready(), {k: s.status(k) for k in ('z', 'a', 'c', 'free')})
            with self.assertRaises(error):
                s.replace_deps('a', deps)
            self.assertEqual((s.ready(), {k: s.status(k) for k in ('z', 'a', 'c', 'free')}), before)
            self.assertEqual(s._tasks['a'].deps, frozenset(['z']))
        s.start('z')
        s.finish('z')
        self.assertIn('a', s.ready())

    def test_state_guards(self):
        s = self.s
        with self.assertRaises(StateError):
            s.start('unknown')
        s.add('x')
        for op in (lambda: s.finish('x'), lambda: s.retry('x')):
            with self.assertRaises(StateError):
                op()
        s.start('x')
        for op in (lambda: s.start('x'), lambda: s.retry('x'),
                   lambda: s.replace_deps('x', [])):
            with self.assertRaises(StateError):
                op()
        s.finish('x', False)
        for op in (lambda: s.start('x'), lambda: s.finish('x'),
                   lambda: s.replace_deps('x', [])):
            with self.assertRaises(StateError):
                op()
        s.retry('x')
        s.start('x')
        s.finish('x')
        for op in (lambda: s.start('x'), lambda: s.finish('x'),
                   lambda: s.retry('x'), lambda: s.replace_deps('x', [])):
            with self.assertRaises(StateError):
                op()

    def test_add_invalid_is_atomic(self):
        s = self.s
        s.add('existing')
        with self.assertRaises(ValueError):
            s.add('bad', ['unknown'])
        with self.assertRaises(CycleError):
            s.add('self', ['self'])
        with self.assertRaises(ValueError):
            s.add('existing')
        self.assertEqual(s.ready(), ['existing'])
        for op in (lambda: s.status('unknown'), lambda: s.replace_deps('unknown', [])):
            with self.assertRaises(ValueError):
                op()

    def test_cycle_check_is_iterative_for_deep_graph(self):
        s = self.s
        s.add('0')
        for i in range(1, 1100):
            s.add(str(i), [str(i - 1)])
        with self.assertRaises(CycleError):
            s.replace_deps('0', ['1099'])
        self.assertEqual(s.ready(), ['0'])


if __name__ == '__main__':
    unittest.main()
