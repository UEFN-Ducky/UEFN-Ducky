// Testing Library's findBy* / waitFor give up after 1 s by default. That is a wall clock, not
// a check: on a busy PC (a release run with UEFN open, ~15 s to start each test file) a
// button that does appear came too late and the test failed. What each test waits for is
// unchanged; it may only wait longer before saying it never came.
import { configure } from "@testing-library/dom";

configure({ asyncUtilTimeout: 10_000 });
