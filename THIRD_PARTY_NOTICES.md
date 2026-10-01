# Third-party notices

Wife NG's original code is MIT licensed. Third-party components retain their own licenses.

| Component | Source | License / handling |
| --- | --- | --- |
| Fabric example scaffold | https://github.com/FabricMC/fabric-example-mod | CC0-1.0; original license retained in docs-template-CC0.txt |
| Baritone 1.6.3 | https://github.com/cabaletta/baritone/tree/v1.6.3 | LGPL-3.0; downloaded separately, not bundled in the mod or repository |
| Fabric Loader / API / Loom | https://github.com/FabricMC | See each upstream project; obtained by Gradle |
| Gradle wrapper | https://github.com/gradle/gradle | Apache-2.0; wrapper files retained from the scaffold |
| HeadlessMC (optional) | https://github.com/headlesshq/headlessmc | See upstream; separately installed, not redistributed |
| Minecraft | https://www.minecraft.net/ | Proprietary; game, assets, server and account data are not redistributed |

Baritone remains a replaceable external Fabric mod. Its source and license are available at the link above; use an interface-compatible modified build if desired. The HeadlessMC CI marker in src/main/java/me/earth/mc_runtime_test is a project-written compatibility shim, not the upstream runtime-test implementation.
