"""Domain layer tests: spec -> controls, and probe -> availability rules."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import availability, model  # noqa: E402
from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "specs")


def load_spec(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return spec_mod.parse_spec(json.load(handle))


class TestControls(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec("dishwasher_s2.json")
        self.controls = model.build_controls(self.spec)

    def test_diagnostics_hidden_by_default(self):
        names = [c.name for c in self.controls]
        self.assertNotIn("manufacturer", names)
        self.assertIn("mode", names)

    def test_diagnostics_can_be_included(self):
        with_diag = model.build_controls(self.spec, include_diagnostics=True)
        self.assertGreater(len(with_diag), len(self.controls))
        self.assertIn("manufacturer", [c.name for c in with_diag])

    def test_bool_becomes_switch(self):
        power = [c for c in self.controls if c.name == "on"][0]
        self.assertEqual(power.widget, "switch")
        self.assertEqual(power.format, "bool")
        self.assertTrue(power.writable)

    def test_enum_becomes_buttons_or_select(self):
        mode = [c for c in self.controls if c.name == "mode"][0]
        self.assertIn(mode.widget, ("buttons", "select"))
        self.assertTrue(mode.options)
        self.assertTrue(all(isinstance(v, int) for v, _ in mode.options))

    def test_readonly_numeric_widget(self):
        left = [c for c in self.controls if c.name == "left-time"][0]
        self.assertFalse(left.writable)
        self.assertEqual(left.unit, "minutes")
        # read-only telemetry renders as a progress bar, never as a slider
        self.assertEqual(left.widget, "progress")

    def test_address_roundtrip(self):
        for control in self.controls:
            self.assertEqual(control.address, "%d.%d" % (control.siid, control.piid))
            self.assertEqual(control.key, (control.siid, control.piid))

    def test_primary_is_power(self):
        primary = model.select_primary(self.spec)
        self.assertIsNotNone(primary)
        self.assertEqual(primary.name, "on")

    def test_primary_none_without_writable_controls(self):
        class Empty:
            services = ()
        self.assertIsNone(model.select_primary(Empty))

    def test_control_lookup(self):
        view = model.DeviceView("did1", "xiaomi.dishwasher.s2", "洗碗机", True, self.spec)
        self.assertIsNotNone(view.control(2, 4))
        self.assertIsNone(view.control(9, 9))


class TestDeviceView(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec("dishwasher_s2.json")
        self.view = model.DeviceView(
            "did1", "xiaomi.dishwasher.s2", "台面洗碗机", True, self.spec,
            catalog={"n": "米家台式洗碗机 S2", "c": "厨房电器", "ic": "http://x/y.png"},
            layout={"l": 3})

    def test_display_name_prefers_user_name(self):
        self.assertEqual(self.view.display_name, "台面洗碗机")

    def test_category_icon_layout(self):
        self.assertEqual(self.view.category, "厨房电器")
        self.assertEqual(self.view.icon, "http://x/y.png")
        self.assertEqual(self.view.card_layout, 3)

    def test_read_items_cover_all_readables(self):
        items = self.view.read_items()
        self.assertEqual(len(items), len(self.spec.readable_properties()))
        self.assertTrue(all(item[0] == "did1" for item in items))

    def test_poll_items_are_a_subset(self):
        poll = self.view.poll_items()
        self.assertLessEqual(len(poll), len(self.view.read_items()))
        self.assertTrue(all(len(item) == 3 for item in poll))

    def test_groups_cover_every_control(self):
        grouped = sum(len(g["writable"]) + len(g["readonly"])
                      for g in self.view.groups())
        self.assertEqual(grouped, len(self.view.controls))

    def test_groups_split_settable_from_readable(self):
        """A row you can edit must never look like a telemetry readout."""
        for group in self.view.groups():
            for control in group["writable"]:
                self.assertTrue(control.writable, control.name)
            for control in group["readonly"]:
                self.assertFalse(control.writable, control.name)

    def test_to_dict_is_json_serializable(self):
        text = json.dumps(self.view.to_dict(), ensure_ascii=False)
        self.assertIn("洗碗机", text)
        parsed = json.loads(text)
        self.assertEqual(parsed["did"], "did1")
        self.assertTrue(parsed["controls"])

    def test_view_without_spec(self):
        view = model.DeviceView("did2", "unknown.model", "", False, None)
        self.assertEqual(view.controls, [])
        self.assertIsNone(view.primary)
        self.assertEqual(view.read_items(), [])


class TestCatalogLoaders(unittest.TestCase):
    def test_known_model_entry(self):
        entry = model.load_catalog_entry("xiaomi.dishwasher.s2")
        if not entry:
            self.skipTest("catalog asset not generated")
        self.assertTrue(entry.get("n"))
        self.assertTrue(entry.get("ic", "").startswith("http"))

    def test_unknown_model_is_empty(self):
        self.assertEqual(model.load_catalog_entry("no.such.model"), {})

    def test_card_layout_lookup(self):
        layouts = model.load_card_layout("xiaomi.aircondition.ma1")
        if not layouts:
            self.skipTest("card layout asset not generated")
        self.assertIn("l", layouts)


class TestBlockerEvaluation(unittest.TestCase):
    """The dishwasher defect from the handoff, expressed as a rule."""

    def setUp(self):
        self.availability = availability.Availability("xiaomi.dishwasher.s2", [
            availability.Blocker({"2.2": [6]}, ["2.4"],
                                 reason="关机状态下不可设置模式"),
        ])

    def test_disabled_when_off(self):
        state = {(2, 2): 6}
        self.assertFalse(self.availability.is_enabled((2, 4), state))
        self.assertEqual(self.availability.reason_for((2, 4), state),
                         "关机状态下不可设置模式")

    def test_enabled_when_idle(self):
        state = {(2, 2): 1}
        self.assertTrue(self.availability.is_enabled((2, 4), state))
        self.assertEqual(self.availability.reason_for((2, 4), state), "")

    def test_unknown_state_does_not_block(self):
        self.assertTrue(self.availability.is_enabled((2, 4), {}))

    def test_string_state_keys_accepted(self):
        self.assertFalse(self.availability.is_enabled("2.4", {"2.2": 6}))

    def test_unrelated_control_untouched(self):
        self.assertTrue(self.availability.is_enabled((2, 1), {(2, 2): 6}))

    def test_roundtrip(self):
        payload = self.availability.to_dict()
        rebuilt = availability.Availability.from_dict(payload)
        self.assertEqual(rebuilt.model, self.availability.model)
        self.assertEqual(len(rebuilt.blockers), 1)
        self.assertFalse(rebuilt.is_enabled((2, 4), {(2, 2): 6}))

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "avail.json")
            self.availability.save(path)
            loaded = availability.Availability.load(path)
            self.assertEqual(loaded.to_dict(), self.availability.to_dict())


class TestProbePlanning(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec("dishwasher_s2.json")

    def test_states_derived_from_power(self):
        steps = availability.plan_probe(self.spec)
        self.assertTrue(steps)
        state_values = {step.state_value for step in steps}
        self.assertEqual(state_values, {False, True})

    def test_each_step_has_a_distinct_target(self):
        steps = availability.plan_probe(self.spec)
        targets = {step.target for step in steps}
        self.assertGreater(len(targets), 1)

    def test_skip_removes_targets(self):
        all_steps = availability.plan_probe(self.spec)
        skipped = availability.plan_probe(self.spec, skip=[(2, 4)])
        self.assertNotIn((2, 4), {step.target for step in skipped})
        self.assertLess(len(skipped), len(all_steps))

    def test_max_targets(self):
        steps = availability.plan_probe(self.spec, max_targets=1)
        self.assertEqual(len({step.target for step in steps}), 1)

    def test_step_serialization(self):
        step = availability.plan_probe(self.spec)[0]
        payload = step.to_dict()
        self.assertIn(".", payload["target"])
        self.assertIn("state", payload)

    def test_current_values_make_probes_no_op(self):
        """With the current state supplied, probes write the value it already has."""
        current = {(2, 4): 3, (2, 1): True}
        steps = availability.plan_probe(self.spec, current=current)
        mode_steps = [s for s in steps if s.target == (2, 4)]
        self.assertTrue(mode_steps)
        self.assertEqual({s.probe_value for s in mode_steps}, {3})
        # the power property is the state variable, so it is never a probe target
        self.assertEqual([s for s in steps if s.target == (2, 1)], [])

    def test_without_current_uses_legal_guess(self):
        steps = availability.plan_probe(self.spec)
        mode_steps = [s for s in steps if s.target == (2, 4)]
        mode = self.spec.find_property("dishwasher", "mode")
        self.assertEqual({s.probe_value for s in mode_steps},
                         {mode.value_list[0].value})


class TestDeriveBlockers(unittest.TestCase):
    """A synthetic matrix shaped like the real dishwasher refusal."""

    def setUp(self):
        self.matrix = availability.ProbeMatrix("xiaomi.dishwasher.s2")
        # status = 6 (off): mode refused
        self.matrix.record((2, 4), (2, 2), 6, -5000)
        # status = 1 (idle): mode accepted
        self.matrix.record((2, 4), (2, 2), 1, 0)
        # status = 2 (busy): mode accepted
        self.matrix.record((2, 4), (2, 2), 2, 0)
        # power switch: refused while off, accepted while on
        self.matrix.record((2, 1), (2, 2), 6, -5000)
        self.matrix.record((2, 1), (2, 2), 1, 0)

    def test_derives_rules_for_both_controls(self):
        blockers = availability.derive_blockers(self.matrix)
        disabled = set()
        for blocker in blockers:
            disabled |= blocker.disabled
        self.assertIn((2, 4), disabled)
        self.assertIn((2, 1), disabled)

    def test_rule_matches_only_the_off_state(self):
        blockers = availability.derive_blockers(self.matrix)
        rule = [b for b in blockers if (2, 4) in b.disabled][0]
        self.assertEqual(rule.when, {(2, 2): frozenset([6])})
        self.assertTrue(rule.matches({(2, 2): 6}))
        self.assertFalse(rule.matches({(2, 2): 1}))

    def test_applied_to_evaluator(self):
        blockers = availability.derive_blockers(self.matrix)
        evaluator = availability.Availability("m", blockers)
        self.assertFalse(evaluator.is_enabled((2, 4), {(2, 2): 6}))
        self.assertTrue(evaluator.is_enabled((2, 4), {(2, 2): 1}))

    def test_no_rule_when_always_accepted(self):
        matrix = availability.ProbeMatrix()
        matrix.record((2, 4), (2, 2), 6, 0)
        matrix.record((2, 4), (2, 2), 1, 0)
        self.assertEqual(availability.derive_blockers(matrix), [])

    def test_no_rule_when_never_accepted(self):
        """A control that always fails needs a human, not a state rule."""
        matrix = availability.ProbeMatrix()
        matrix.record((7, 9), (2, 2), 6, -5000)
        matrix.record((7, 9), (2, 2), 1, -5000)
        self.assertEqual(availability.derive_blockers(matrix), [])

    def test_always_failing_targets_reported(self):
        matrix = availability.ProbeMatrix()
        matrix.record((7, 9), (2, 2), 6, -5000)
        matrix.record((7, 9), (2, 2), 1, -704053100)
        self.assertEqual(matrix.always_failing_targets(), ["7.9"])

    def test_outcome_table(self):
        table = self.matrix.outcome((2, 4))
        self.assertTrue(table[((2, 2), 1)])
        self.assertFalse(table[((2, 2), 6)])

    def test_rejected_states(self):
        rejected = self.matrix.rejected_states()
        self.assertIn((2, 4), rejected)
        self.assertTrue(rejected[(2, 4)].get(6))
        self.assertFalse(rejected[(2, 4)].get(1))


class TestReadBackDerivation(unittest.TestCase):
    """Real devices accept-and-ignore, so read-back must drive the derivation.

    Measured on minij.washer.v5 (2026-11-19): while a cycle ran, four property
    writes all answered code 0 and none of the values changed.  A matrix built
    from codes alone would therefore find nothing.
    """

    def test_code_zero_but_not_applied_becomes_a_refusal(self):
        matrix = availability.ProbeMatrix("m")
        # idle: written and actually landed
        matrix.record((2, 3), (2, 2), 2, 0, applied=True)
        # busy: device said 0 but the value never changed
        matrix.record((2, 3), (2, 2), 3, 0, applied=False)
        blockers = availability.derive_blockers(matrix)
        self.assertEqual(len(blockers), 1, blockers)
        rule = blockers[0]
        self.assertEqual(rule.when, {(2, 2): frozenset([3])})
        self.assertIn((2, 3), rule.disabled)

    def test_applied_beats_the_code(self):
        matrix = availability.ProbeMatrix("m")
        matrix.record((2, 3), (2, 2), 2, -5000, applied=True)   # code lied low
        matrix.record((2, 3), (2, 2), 3, 0, applied=False)      # code lied high
        blockers = availability.derive_blockers(matrix)
        self.assertEqual(len(blockers), 1, blockers)
        self.assertEqual(blockers[0].when, {(2, 2): frozenset([3])})

    def test_without_readback_the_code_path_still_works(self):
        matrix = availability.ProbeMatrix("m")
        matrix.record((2, 4), (2, 2), 1, 0)
        matrix.record((2, 4), (2, 2), 6, -5000)
        blockers = availability.derive_blockers(matrix)
        self.assertEqual(len(blockers), 1, blockers)
        self.assertEqual(blockers[0].when, {(2, 2): frozenset([6])})

    def test_outcome_prefers_readback(self):
        matrix = availability.ProbeMatrix("m")
        matrix.record((2, 3), (2, 2), 3, 0, applied=False)
        self.assertEqual(matrix.outcome((2, 3))[((2, 2), 3)], False)

    def test_always_failing_uses_readback(self):
        matrix = availability.ProbeMatrix("m")
        matrix.record((9, 9), (2, 2), 2, 0, applied=False)
        matrix.record((9, 9), (2, 2), 3, 0, applied=False)
        self.assertEqual(matrix.always_failing_targets(), ["9.9"])


class TestRejectionRule(unittest.TestCase):
    """Any negative device code counts; cloud "unconfirmed" codes do not."""

    def test_measured_code_is_a_rejection(self):
        # the code a real dishwasher returned when asked to change mode while on
        self.assertTrue(availability.is_rejection(-704220043))

    def test_legacy_codes_still_count(self):
        for code in (-5000, -704053100, -704042011, -704030013):
            self.assertTrue(availability.is_rejection(code))

    def test_unconfirmed_is_not_a_rejection(self):
        for code in (-704010000, -10000):
            self.assertFalse(availability.is_rejection(code))

    def test_success_is_not_a_rejection(self):
        self.assertFalse(availability.is_rejection(0))
        self.assertFalse(availability.is_rejection(1))

    def test_garbage_does_not_raise(self):
        self.assertFalse(availability.is_rejection(None))
        self.assertFalse(availability.is_rejection("nope"))


class TestWasherModes(unittest.TestCase):
    """A washer's 22 programmes must all read in Chinese.

    Real device minij.washer.v5: the spec ships English descriptions only and
    the i18n table is 45 bytes, so every label comes from our dictionary.
    """

    def setUp(self):
        self.spec = load_spec("washer_v5.json")
        self.view = model.DeviceView("d", "minij.washer.v5", "w", True, self.spec)

    def _mode(self):
        for control in self.view.controls:
            if control.name == "mode":
                return control
        return None

    def test_every_mode_is_chinese(self):
        mode = self._mode()
        self.assertIsNotNone(mode)
        self.assertEqual(len(mode.options), 22)
        latin = [(value, label) for value, label in mode.options
                 if not any("\u4e00" <= ch <= "\u9fff" for ch in label)]
        self.assertFalse(latin, latin)

    def test_wash_dry_programmes_exist(self):
        labels = dict(self._mode().options)
        self.assertIn("\u70d8", labels[18])           # Wash Dry  -> 洗+烘
        self.assertIn("\u70d8", labels[22])           # Quick Wash Dry -> 快洗烘
        self.assertIn("\u5feb", labels[22])

    def test_dry_means_drying_on_a_washer(self):
        # "Dry" is 烘干 on a washer but 除湿 on an air conditioner
        labels = dict(self._mode().options)
        self.assertEqual(labels[20], "\u70d8\u5e72")

    def test_dry_stays_dehumidify_on_an_air_conditioner(self):
        spec = load_spec("airconditioner_ma1.json")
        view = model.DeviceView("d", "xiaomi.aircondition.ma1", "ac", True, spec)
        for control in view.controls:
            if control.name == "mode" and control.options:
                labels = [label for _, label in control.options]
                self.assertIn("\u9664\u6e7f", labels)      # 除湿
                self.assertNotIn("\u70d8\u5e72", labels)    # not 烘干
                return
        self.fail("air conditioner has no mode enum")

    def test_spec_exposes_device_type(self):
        self.assertEqual(self.spec.device_type, "washer")


class TestBulbFamilyFixes(unittest.TestCase):
    """Regressions found while testing a real Yeelight bulb (2026-11-19)."""

    def setUp(self):
        self.spec = load_spec("dishwasher_s2.json")

    def test_write_only_property_becomes_a_command(self):
        """``toggle``/``bright-scene`` are write-only; a stepper looked broken."""
        controls = model.build_controls(self.spec, include_diagnostics=True)
        self.assertTrue(controls)
        for control in controls:
            if not control.readable:
                self.assertEqual(control.widget, "command", control.name)
                self.assertIsNotNone(control.command_value)
            else:
                self.assertNotEqual(control.widget, "command", control.name)

    def test_percentage_unit_dropped_on_raw_scale(self):
        # Yeelight declares percentage but exposes 1..65535 -> must not say "%"
        self.assertEqual(model.unit_label("percentage", (1, 65535, 1)), "")
        self.assertEqual(model.unit_label("percentage", (0, 100, 1)), "%")
        self.assertEqual(model.unit_label("minutes"), "\u5206\u949f")
        self.assertEqual(model.unit_label(None), "")

    def test_labels_have_no_embedded_whitespace(self):
        for control in model.build_controls(self.spec, include_diagnostics=True):
            self.assertEqual(control.label, " ".join(control.label.split()))


class TestKeyParsing(unittest.TestCase):
    def test_variants(self):
        for value in ("2.4", (2, 4), [2, 4]):
            self.assertEqual(availability._key(value), (2, 4))

    def test_bad_input(self):
        self.assertRaises(ValueError, availability._key, 24)

    def test_format(self):
        self.assertEqual(availability.format_key((2, 4)), "2.4")


class TestRemotePanelOwnership(unittest.TestCase):
    """Remote-panel properties stay in ``controls`` but leave the generic list.

    Dropping them from ``controls`` (the pre-2026-11-21 behaviour) made a TV
    show zero controls, poll zero properties and therefore display no state at
    all, and the remote panel could not find the controls it binds to.
    """

    def setUp(self):
        self.spec = load_spec("television_fsv1.json")
        i18n = os.path.join(FIXTURES, "television_fsv1.i18n.json")
        if os.path.exists(i18n):
            with open(i18n, encoding="utf-8") as handle:
                spec_mod.apply_i18n(self.spec, json.load(handle).get("data") or {})
        self.view = model.DeviceView("tv1", "xiaomi.tv.fsv1", "电视", True, self.spec)

    def test_the_remote_exists_and_owns_three_values(self):
        self.assertIsNotNone(self.view.remote)
        self.assertEqual(self.view.remote_keys, {(2, 1), (4, 1), (4, 2)})

    def test_the_panel_can_find_its_controls(self):
        for key in self.view.remote_keys:
            self.assertIsNotNone(self.view.control(*key))

    def test_owned_values_are_still_polled(self):
        polled = {(siid, piid) for _did, siid, piid in self.view.poll_items()}
        self.assertTrue(self.view.remote_keys.issubset(polled))

    def test_they_are_not_repeated_as_generic_rows(self):
        grouped = [(c.siid, c.piid) for group in self.view.groups()
                   for c in group["writable"] + group["readonly"]]
        self.assertEqual(grouped, [])
        self.assertEqual(self.view.status_controls, [])

    def test_primary_never_picks_a_remote_property(self):
        # a TV has no ``on`` bool; the card used to fall back to input-control
        self.assertIsNone(self.view.primary)

    def test_message_router_is_not_an_action(self):
        # siid 5 is the firmware's own post/response RPC, not something a user
        # can run: it used to render as a 「消息传递 / 执行」 button
        names = [action.name for action in self.view.actions]
        self.assertNotIn("post", names)
        self.assertEqual([c.name for c in self.view.controls if c.siid == 5], [])


if __name__ == "__main__":
    unittest.main()
