"""Tests for the stops of «check plate» (worker/run_job.py › parse_check + residue_stop), no media files.

    python -m unittest discover -s tests"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker'))
import run_job as rj


def row(zone, mode, frames, t0=1.0, t1=5.0, worst_t=2.0, px=200):
    return [zone, mode, t0, t1, frames, worst_t, px]


def chk(*rows):
    return {'residue': list(rows), 'offText': 0}


# The residue of every finished job replayed when the zone stop was added (zone, mode, t0, t1, frames, worst t, px).
# BAD = plates that kept old letters outside the captions (a half-erased headline + a line left in the old language;
# an old line under the new caption; old words between pages); CLEAN = every other plate of the same runs.
BAD = [
    [[0, 'dark', 0.0, 4.17, 119, 3.93, 598], [1, 'dark', 0.0, 4.17, 1, 3.93, 852], [5, 'light', 0.0, 21.5, 1, 9.33, 224],
     [6, 'dark', 9.13, 21.5, 370, 9.33, 959]],
    [[10, 'dark', 26.6, 30.5, 17, 30.27, 202], [38, 'light', 8.8, 9.9, 1, 9.87, 43], [101, 'light', 45.8, 46.8, 1, 45.8, 43]],
    [[13, 'light', 40.1, 47.57, 1, 45.6, 53], [20, 'light', 1.8, 3.0, 1, 1.93, 46], [34, 'light', 10.6, 11.6, 1, 11.1, 88],
     [35, 'light', 11.8, 13.1, 34, 12.9, 150], [43, 'light', 14.3, 15.3, 28, 15.03, 322], [44, 'light', 15.2, 15.9, 19, 15.3, 324],
     [70, 'light', 29.5, 30.8, 1, 29.8, 181], [73, 'light', 31.9, 32.4, 6, 32.2, 104], [75, 'light', 32.8, 33.8, 6, 33.67, 77]],
]
CLEAN = [[[2, 'dark', 10.7, 23.0, 2, 10.7, 2188]], [[2, 'light', 14.3, 27.4, 8, 15.57, 49]],
         [[3, 'light', 43.7, 45.8, 2, 43.7, 70]]] + [[]] * 15


class ResidueStop(unittest.TestCase):
    def test_a_headline_left_in_15_frames_or_more_stops_the_job_and_names_the_worst_zone(self):
        why = rj.residue_stop(chk(row(0, 'dark', 119), row(6, 'dark', 370, 9.13, 21.5, 9.33, 959), row(5, 'light', 1)))
        self.assertIn('outside the captions', why)
        self.assertIn('2 zone(s)', why)
        self.assertIn('zone 6 (dark, 9.13-21.50 s) in 370 frames, worst frame at 9.33 s', why)
        self.assertIsNotNone(rj.residue_stop(chk(row(3, 'both', 15))))

    def test_a_few_frames_of_residue_in_a_zone_stay_a_note(self):
        self.assertIsNone(rj.residue_stop(chk(row(2, 'dark', 2), row(7, 'light', 14), row(8, 'both', 8))))
        self.assertIsNone(rj.residue_stop(chk()))

    def test_the_zone_threshold_can_be_set_per_spec(self):
        self.assertIsNone(rj.residue_stop(chk(row(3, 'light', 17)), zone_frames=20))
        self.assertIsNotNone(rj.residue_stop(chk(row(3, 'light', 8)), zone_frames=8))

    def test_caption_and_still_stops_keep_their_rules_and_come_first(self):
        why = rj.residue_stop(chk(row(1, 'cap', 1, 0.24), row(6, 'dark', 370)))
        self.assertTrue(why.startswith('old caption letters left on the plate in 1 frames (first at 0.24 s)'))
        self.assertIn('a line that stays through the video', rj.residue_stop(chk(row(1000, 'still', 3))))
        self.assertIsNone(rj.residue_stop(chk(row(1000, 'still', 2))))
        self.assertIsNone(rj.residue_stop(chk(row(1000, 'still', 2)), zone_frames=1), 'a still text keeps its own 3-frame rule')

    def test_replay_of_recorded_checks_stops_every_bad_plate_and_no_clean_one(self):
        self.assertEqual([rj.residue_stop(chk(*r)) is not None for r in BAD], [True] * 3)
        self.assertEqual(sum(rj.residue_stop(chk(*r)) is not None for r in CLEAN), 0)
        self.assertEqual(len(CLEAN), 18)


class ParseCheck(unittest.TestCase):
    def test_the_check_log_becomes_rows_and_a_zone_stop(self):
        text = ('static masks: {1: (253, 35979)}\n'
                'zone 6 dark 9.13-21.50 rect [0, 100, 1080, 300]: 370 frames, worst (9.33, 959)\n'
                'zone 1001 still 0.00-30.00 rect [40, 1700, 1040, 1760]: 2 frames, worst (4.10, 120)\n'
                'zone 0 cap 0.00-47.15 rect [0, 1150, 1080, 1367]: 212 frames, worst (24.07, 1921) (a glyph left next to the erased line)\n'
                'check: no blur off the text\n')
        c = rj.parse_check(text)
        self.assertEqual(c['residue'][0], [6, 'dark', 9.13, 21.5, 370, 9.33, 959])
        self.assertEqual([r[1] for r in c['residue']], ['dark', 'still', 'cap'])
        self.assertTrue(rj.residue_stop(c).startswith('old caption letters'))
        c['residue'] = [r for r in c['residue'] if r[1] != 'cap']
        self.assertIn('zone 6 (dark', rj.residue_stop(c))


if __name__ == '__main__':
    unittest.main()
