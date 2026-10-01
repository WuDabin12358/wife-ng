package me.earth.mc_runtime_test;

/**
 * Marker class for HeadlessMC's CI detection (CICheck).
 *
 * When the game is launched from a process that has no attached console
 * (for example when an automation agent starts it), JLine cannot create a
 * real terminal and HeadlessMC's VersionAgnosticJLineCommandLineReader
 * crashes during startup with "Failed to start JLineCommandLineReader".
 *
 * CICheck calls {@code Class.forName("me.earth.mc_runtime_test.McRuntimeTest")}
 * and, when this class is present, falls back to a dumb terminal that reads
 * commands from System.in, which keeps headless launches working.
 *
 * This is the same trick HeadlessMC itself uses in CI environments.
 */
public final class McRuntimeTest {
    private McRuntimeTest() {
    }
}
