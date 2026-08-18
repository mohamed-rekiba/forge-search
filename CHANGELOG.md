# Changelog

## [1.2.0](https://github.com/mohamed-rekiba/forge-search/compare/v1.1.0...v1.2.0) (2026-08-18)


### Features

* fall back to the working directory when no --root is given, audibly ([#59](https://github.com/mohamed-rekiba/forge-search/issues/59)) ([8c7858c](https://github.com/mohamed-rekiba/forge-search/commit/8c7858cc1f36cd1cf7ca23359b5ff759fb194f8e))

## [1.1.0](https://github.com/mohamed-rekiba/forge-search/compare/v1.0.1...v1.1.0) (2026-08-18)


### Features

* count_matches — complete enumeration, priced in paths ([#57](https://github.com/mohamed-rekiba/forge-search/issues/57)) ([aa162ea](https://github.com/mohamed-rekiba/forge-search/commit/aa162ea1c1b1a744e8ed515f9ca4f8e8ca2bf462))

## [1.0.1](https://github.com/mohamed-rekiba/forge-search/compare/v1.0.0...v1.0.1) (2026-08-18)


### Bug Fixes

* three silent-shortfall paths in the result pipeline ([#55](https://github.com/mohamed-rekiba/forge-search/issues/55)) ([faef5f7](https://github.com/mohamed-rekiba/forge-search/commit/faef5f7544f20edf914efddccb055eb02bb7a9e4))

## [1.0.0](https://github.com/mohamed-rekiba/forge-search/compare/v0.6.0...v1.0.0) (2026-08-02)


### ⚠ BREAKING CHANGES

* **#45:** replace the apexyard root adapter with --sub-projects ([#46](https://github.com/mohamed-rekiba/forge-search/issues/46))

### Refactors

* **#45:** replace the apexyard root adapter with --sub-projects ([#46](https://github.com/mohamed-rekiba/forge-search/issues/46)) ([be1e1f8](https://github.com/mohamed-rekiba/forge-search/commit/be1e1f88c8b9b49389a2704ca9be2099b0923189))

## [0.6.0](https://github.com/mohamed-rekiba/forge-search/compare/v0.5.0...v0.6.0) (2026-07-29)


### Features

* **#25:** report index refresh age, fix relative root paths, and add a coverage floor ([#38](https://github.com/mohamed-rekiba/forge-search/issues/38)) ([0a2d885](https://github.com/mohamed-rekiba/forge-search/commit/0a2d88583125fdf90163517be60a93fadbc59e17))

## [0.5.0](https://github.com/mohamed-rekiba/forge-search/compare/v0.4.2...v0.5.0) (2026-07-29)


### Features

* **#21:** add a doctor command that reports index health ([#31](https://github.com/mohamed-rekiba/forge-search/issues/31)) ([581fba1](https://github.com/mohamed-rekiba/forge-search/commit/581fba14f22f528b5eb6a7fbb01b9913ac3c208c)), closes [#21](https://github.com/mohamed-rekiba/forge-search/issues/21)
* **#22:** add a reindex command that can skip recently-refreshed indexes ([#34](https://github.com/mohamed-rekiba/forge-search/issues/34)) ([df72d18](https://github.com/mohamed-rekiba/forge-search/commit/df72d188feb1594931530637f4e15af83fb05f64)), closes [#22](https://github.com/mohamed-rekiba/forge-search/issues/22)


### Bug Fixes

* **#23:** restore fresh installs by moving to the current MCP SDK ([#24](https://github.com/mohamed-rekiba/forge-search/issues/24)) ([e854efc](https://github.com/mohamed-rekiba/forge-search/commit/e854efcd46528922bb84a1b1461b8584979de8d2)), closes [#23](https://github.com/mohamed-rekiba/forge-search/issues/23)
* pin setup-uv to v9.0.0 — it publishes no major tag ([89ea1b2](https://github.com/mohamed-rekiba/forge-search/commit/89ea1b22dda82e2a87190ec38399571a1f542fa7))


### Documentation

* **#35:** document the doctor and reindex commands, and the portfolio MCP config ([#36](https://github.com/mohamed-rekiba/forge-search/issues/36)) ([0bce47d](https://github.com/mohamed-rekiba/forge-search/commit/0bce47d01a0a6d0151f3fbd5eef712e6d2bb4578))
* record the action-pinning decision ([f220a6e](https://github.com/mohamed-rekiba/forge-search/commit/f220a6e884bd1f74f115b804918d98a5b026bd7c))

## [0.4.2](https://github.com/mohamed-rekiba/forge-search/compare/v0.4.1...v0.4.2) (2026-07-21)


### Bug Fixes

* discover roots registered after server startup ([a760851](https://github.com/mohamed-rekiba/forge-search/commit/a760851676a89b451cdde2b49c2bf2f6f10846d1))

## [0.4.1](https://github.com/mohamed-rekiba/forge-search/compare/v0.4.0...v0.4.1) (2026-07-21)


### Refactors

* update workspace path handling for compatibility ([82525eb](https://github.com/mohamed-rekiba/forge-search/commit/82525eb425b1c889ba90a2acf517594bf3d2fc08))


### Documentation

* add install-from-source command ([b5e0786](https://github.com/mohamed-rekiba/forge-search/commit/b5e0786ddd6366ba0849c764a6dd5202ef47ee9b))
* split the README into a front door and a documentation set ([4c8a96b](https://github.com/mohamed-rekiba/forge-search/commit/4c8a96b5e0caefcb4bfeec82cffe17290958bf01))

## [0.4.0](https://github.com/mohamed-rekiba/forge-search/compare/v0.3.0...v0.4.0) (2026-07-21)


### Features

* --index-dir for in-tree indexes, and quieten the MCP request log ([e4cb464](https://github.com/mohamed-rekiba/forge-search/commit/e4cb464462291aa538f56a037759f78b3177da1a))
* ApexYard root provider — the first adapter, and a test of the seam ([0c21f06](https://github.com/mohamed-rekiba/forge-search/commit/0c21f0643e2153c0db101529dff87d8d2ffe914c))
* breadcrumb every chunk — semantic recall 50% -&gt; 87.5% ([aff06ef](https://github.com/mohamed-rekiba/forge-search/commit/aff06ef0b4f3903754741271bee4159ad86df081))
* cross-encoder reranking, off by default because it lost ([4eb1480](https://github.com/mohamed-rekiba/forge-search/commit/4eb1480e63c771fed42d0db401455da2b758733b))
* filename matching, and schema versioning to stop shipping silent gaps ([208e056](https://github.com/mohamed-rekiba/forge-search/commit/208e05646c50bc6aff412ee68f9c6a4d88b5d7e6))
* forge-search — local code and docs search as an MCP server ([2f83aa9](https://github.com/mohamed-rekiba/forge-search/commit/2f83aa9e90ea2702ca958cd4f2363c2b37e76e08))
* fuse both arms in search_code, and fix a silent semantic backfill bug ([7204c2e](https://github.com/mohamed-rekiba/forge-search/commit/7204c2edfc94ca4a9c90f7c6b4a677133ab450a3))
* index in &lt;root&gt;/.forge_search by default, overridable by the adapter ([c4da5c0](https://github.com/mohamed-rekiba/forge-search/commit/c4da5c095caadf9e85d53db34c8fefe8a33b147d))
* semantic arm — embeddings, vector index, and RRF fusion ([654fc2c](https://github.com/mohamed-rekiba/forge-search/commit/654fc2cf7d191bdea81ba62998e3b0194af47a0f))
* structure-aware chunking — split on functions and headings, not line counts ([1bf9eb1](https://github.com/mohamed-rekiba/forge-search/commit/1bf9eb13c417e5c3c267d60abe1b140b086dbda0))
* warm + bench — explicit model fetch and the measurement that judges the semantic arm ([29f4d6e](https://github.com/mohamed-rekiba/forge-search/commit/29f4d6e7335f33ffaea596511663a2dcae0279c5))


### Bug Fixes

* accept ${workspaceFolder} as a cwd fallback instead of failing ([617b4a9](https://github.com/mohamed-rekiba/forge-search/commit/617b4a918c77ef196fe15fbdb54a7e36e40ad8bc))
* advertise the logging capability, or the notification reaches nobody ([c060149](https://github.com/mohamed-rekiba/forge-search/commit/c0601492b52b14ccd4d9e9351b075808e63777a7))
* announce the config over MCP logging, so it is actually visible ([05e7922](https://github.com/mohamed-rekiba/forge-search/commit/05e7922a884a005146e9868b877a7516db54c657))
* default to line-window chunking — structure-aware measured 12.5pts WORSE ([aff330f](https://github.com/mohamed-rekiba/forge-search/commit/aff330f6f66efc99d42ada2a5bbdbfaf6d76560b))
* discard semantic vectors built by a different model or chunking ([ff01895](https://github.com/mohamed-rekiba/forge-search/commit/ff01895b4b46401bed2d89d8d7d931b61a93ffc1))
* normalise Root paths, and add the clean-install acceptance test that found it ([f39d538](https://github.com/mohamed-rekiba/forge-search/commit/f39d5389d7e5d32ccae1413e01233c551d43da0e))
* per-file summary chunks — semantic recall 0% -&gt; 50%, hybrid 25% -&gt; 62.5% ([1c0f4cc](https://github.com/mohamed-rekiba/forge-search/commit/1c0f4cccfd5b01fab4b58e497b028bd3394cadda))
* print the config to stderr again — the only channel that reaches the client ([9ece5fd](https://github.com/mohamed-rekiba/forge-search/commit/9ece5fd4a27cabf283e5c3938f4b2e7a8b5e901f))
* serve crashed on startup — ForgeSearch never got the index_dir field ([7a93577](https://github.com/mohamed-rekiba/forge-search/commit/7a93577025b6434443b83a37c34431d3e790980b))
* serve is silent on stderr, and names the active adapter ([b01fa90](https://github.com/mohamed-rekiba/forge-search/commit/b01fa9084912b75d495640360d101111fc6532dc))
* stop .gitignore silently excluding the benchmark fixture ([7bf3f40](https://github.com/mohamed-rekiba/forge-search/commit/7bf3f40b3605d084d3796a17a9bea3b3c40ef17f))
* stop recommending ${workspaceFolder}, and isolate the test cache ([64f9167](https://github.com/mohamed-rekiba/forge-search/commit/64f9167332c2a267ab7f150f847fec4a2dc15100))
* stop reporting an adapter expansion that expanded nothing ([bd772ef](https://github.com/mohamed-rekiba/forge-search/commit/bd772ef460340ca353cf30cd0181fea32ec29a5e))


### Refactors

* drop the instructions-field workaround, keep the two real channels ([97abf4d](https://github.com/mohamed-rekiba/forge-search/commit/97abf4d17ad1cdcb3abc29b1c9b2f07ac9696524))
* explicit roots and explicit adapters — remove all discovery ([06fbbef](https://github.com/mohamed-rekiba/forge-search/commit/06fbbef41a8971c4f56c224a83ffd31444eae160))


### Documentation

* label the remaining code fences ([a2459fb](https://github.com/mohamed-rekiba/forge-search/commit/a2459fb730fa1e78f6fdd6a1c77a222394720124))
* README and BENCHMARK caught up with reality ([c7a1af8](https://github.com/mohamed-rekiba/forge-search/commit/c7a1af8ab6d9cad087e709108198fd83b1580dfa))
* README covers filename matching, bench, warm, and schema versioning ([4b6afb4](https://github.com/mohamed-rekiba/forge-search/commit/4b6afb4c070f156d5211576bf39c27e8c76a1991))
* record the semantic benchmark — 0% recall on a real corpus ([23bb457](https://github.com/mohamed-rekiba/forge-search/commit/23bb457707cd0eb360fc056f957b440d41145497))

## [0.3.0](https://github.com/mohamed-rekiba/forge-search/compare/v0.2.0...v0.3.0) (2026-07-21)


### Features

* --index-dir for in-tree indexes, and quieten the MCP request log ([e4cb464](https://github.com/mohamed-rekiba/forge-search/commit/e4cb464462291aa538f56a037759f78b3177da1a))
* ApexYard root provider — the first adapter, and a test of the seam ([0c21f06](https://github.com/mohamed-rekiba/forge-search/commit/0c21f0643e2153c0db101529dff87d8d2ffe914c))
* breadcrumb every chunk — semantic recall 50% -&gt; 87.5% ([aff06ef](https://github.com/mohamed-rekiba/forge-search/commit/aff06ef0b4f3903754741271bee4159ad86df081))
* cross-encoder reranking, off by default because it lost ([4eb1480](https://github.com/mohamed-rekiba/forge-search/commit/4eb1480e63c771fed42d0db401455da2b758733b))
* filename matching, and schema versioning to stop shipping silent gaps ([208e056](https://github.com/mohamed-rekiba/forge-search/commit/208e05646c50bc6aff412ee68f9c6a4d88b5d7e6))
* forge-search — local code and docs search as an MCP server ([2f83aa9](https://github.com/mohamed-rekiba/forge-search/commit/2f83aa9e90ea2702ca958cd4f2363c2b37e76e08))
* fuse both arms in search_code, and fix a silent semantic backfill bug ([7204c2e](https://github.com/mohamed-rekiba/forge-search/commit/7204c2edfc94ca4a9c90f7c6b4a677133ab450a3))
* index in &lt;root&gt;/.forge_search by default, overridable by the adapter ([c4da5c0](https://github.com/mohamed-rekiba/forge-search/commit/c4da5c095caadf9e85d53db34c8fefe8a33b147d))
* semantic arm — embeddings, vector index, and RRF fusion ([654fc2c](https://github.com/mohamed-rekiba/forge-search/commit/654fc2cf7d191bdea81ba62998e3b0194af47a0f))
* structure-aware chunking — split on functions and headings, not line counts ([1bf9eb1](https://github.com/mohamed-rekiba/forge-search/commit/1bf9eb13c417e5c3c267d60abe1b140b086dbda0))
* warm + bench — explicit model fetch and the measurement that judges the semantic arm ([29f4d6e](https://github.com/mohamed-rekiba/forge-search/commit/29f4d6e7335f33ffaea596511663a2dcae0279c5))


### Bug Fixes

* accept ${workspaceFolder} as a cwd fallback instead of failing ([617b4a9](https://github.com/mohamed-rekiba/forge-search/commit/617b4a918c77ef196fe15fbdb54a7e36e40ad8bc))
* advertise the logging capability, or the notification reaches nobody ([c060149](https://github.com/mohamed-rekiba/forge-search/commit/c0601492b52b14ccd4d9e9351b075808e63777a7))
* announce the config over MCP logging, so it is actually visible ([05e7922](https://github.com/mohamed-rekiba/forge-search/commit/05e7922a884a005146e9868b877a7516db54c657))
* default to line-window chunking — structure-aware measured 12.5pts WORSE ([aff330f](https://github.com/mohamed-rekiba/forge-search/commit/aff330f6f66efc99d42ada2a5bbdbfaf6d76560b))
* discard semantic vectors built by a different model or chunking ([ff01895](https://github.com/mohamed-rekiba/forge-search/commit/ff01895b4b46401bed2d89d8d7d931b61a93ffc1))
* normalise Root paths, and add the clean-install acceptance test that found it ([f39d538](https://github.com/mohamed-rekiba/forge-search/commit/f39d5389d7e5d32ccae1413e01233c551d43da0e))
* per-file summary chunks — semantic recall 0% -&gt; 50%, hybrid 25% -&gt; 62.5% ([1c0f4cc](https://github.com/mohamed-rekiba/forge-search/commit/1c0f4cccfd5b01fab4b58e497b028bd3394cadda))
* print the config to stderr again — the only channel that reaches the client ([9ece5fd](https://github.com/mohamed-rekiba/forge-search/commit/9ece5fd4a27cabf283e5c3938f4b2e7a8b5e901f))
* serve crashed on startup — ForgeSearch never got the index_dir field ([7a93577](https://github.com/mohamed-rekiba/forge-search/commit/7a93577025b6434443b83a37c34431d3e790980b))
* serve is silent on stderr, and names the active adapter ([b01fa90](https://github.com/mohamed-rekiba/forge-search/commit/b01fa9084912b75d495640360d101111fc6532dc))
* stop .gitignore silently excluding the benchmark fixture ([7bf3f40](https://github.com/mohamed-rekiba/forge-search/commit/7bf3f40b3605d084d3796a17a9bea3b3c40ef17f))
* stop recommending ${workspaceFolder}, and isolate the test cache ([64f9167](https://github.com/mohamed-rekiba/forge-search/commit/64f9167332c2a267ab7f150f847fec4a2dc15100))
* stop reporting an adapter expansion that expanded nothing ([bd772ef](https://github.com/mohamed-rekiba/forge-search/commit/bd772ef460340ca353cf30cd0181fea32ec29a5e))


### Refactors

* drop the instructions-field workaround, keep the two real channels ([97abf4d](https://github.com/mohamed-rekiba/forge-search/commit/97abf4d17ad1cdcb3abc29b1c9b2f07ac9696524))
* explicit roots and explicit adapters — remove all discovery ([06fbbef](https://github.com/mohamed-rekiba/forge-search/commit/06fbbef41a8971c4f56c224a83ffd31444eae160))


### Documentation

* label the remaining code fences ([a2459fb](https://github.com/mohamed-rekiba/forge-search/commit/a2459fb730fa1e78f6fdd6a1c77a222394720124))
* README and BENCHMARK caught up with reality ([c7a1af8](https://github.com/mohamed-rekiba/forge-search/commit/c7a1af8ab6d9cad087e709108198fd83b1580dfa))
* README covers filename matching, bench, warm, and schema versioning ([4b6afb4](https://github.com/mohamed-rekiba/forge-search/commit/4b6afb4c070f156d5211576bf39c27e8c76a1991))
* record the semantic benchmark — 0% recall on a real corpus ([23bb457](https://github.com/mohamed-rekiba/forge-search/commit/23bb457707cd0eb360fc056f957b440d41145497))

## [0.2.0](https://github.com/mohamed-rekiba/forge-search/compare/forge-search-v0.1.0...forge-search-v0.2.0) (2026-07-21)


### Features

* --index-dir for in-tree indexes, and quieten the MCP request log ([e4cb464](https://github.com/mohamed-rekiba/forge-search/commit/e4cb464462291aa538f56a037759f78b3177da1a))
* ApexYard root provider — the first adapter, and a test of the seam ([0c21f06](https://github.com/mohamed-rekiba/forge-search/commit/0c21f0643e2153c0db101529dff87d8d2ffe914c))
* breadcrumb every chunk — semantic recall 50% -&gt; 87.5% ([aff06ef](https://github.com/mohamed-rekiba/forge-search/commit/aff06ef0b4f3903754741271bee4159ad86df081))
* cross-encoder reranking, off by default because it lost ([4eb1480](https://github.com/mohamed-rekiba/forge-search/commit/4eb1480e63c771fed42d0db401455da2b758733b))
* filename matching, and schema versioning to stop shipping silent gaps ([208e056](https://github.com/mohamed-rekiba/forge-search/commit/208e05646c50bc6aff412ee68f9c6a4d88b5d7e6))
* forge-search — local code and docs search as an MCP server ([2f83aa9](https://github.com/mohamed-rekiba/forge-search/commit/2f83aa9e90ea2702ca958cd4f2363c2b37e76e08))
* fuse both arms in search_code, and fix a silent semantic backfill bug ([7204c2e](https://github.com/mohamed-rekiba/forge-search/commit/7204c2edfc94ca4a9c90f7c6b4a677133ab450a3))
* index in &lt;root&gt;/.forge_search by default, overridable by the adapter ([c4da5c0](https://github.com/mohamed-rekiba/forge-search/commit/c4da5c095caadf9e85d53db34c8fefe8a33b147d))
* semantic arm — embeddings, vector index, and RRF fusion ([654fc2c](https://github.com/mohamed-rekiba/forge-search/commit/654fc2cf7d191bdea81ba62998e3b0194af47a0f))
* structure-aware chunking — split on functions and headings, not line counts ([1bf9eb1](https://github.com/mohamed-rekiba/forge-search/commit/1bf9eb13c417e5c3c267d60abe1b140b086dbda0))
* warm + bench — explicit model fetch and the measurement that judges the semantic arm ([29f4d6e](https://github.com/mohamed-rekiba/forge-search/commit/29f4d6e7335f33ffaea596511663a2dcae0279c5))


### Bug Fixes

* accept ${workspaceFolder} as a cwd fallback instead of failing ([617b4a9](https://github.com/mohamed-rekiba/forge-search/commit/617b4a918c77ef196fe15fbdb54a7e36e40ad8bc))
* advertise the logging capability, or the notification reaches nobody ([c060149](https://github.com/mohamed-rekiba/forge-search/commit/c0601492b52b14ccd4d9e9351b075808e63777a7))
* announce the config over MCP logging, so it is actually visible ([05e7922](https://github.com/mohamed-rekiba/forge-search/commit/05e7922a884a005146e9868b877a7516db54c657))
* default to line-window chunking — structure-aware measured 12.5pts WORSE ([aff330f](https://github.com/mohamed-rekiba/forge-search/commit/aff330f6f66efc99d42ada2a5bbdbfaf6d76560b))
* discard semantic vectors built by a different model or chunking ([ff01895](https://github.com/mohamed-rekiba/forge-search/commit/ff01895b4b46401bed2d89d8d7d931b61a93ffc1))
* normalise Root paths, and add the clean-install acceptance test that found it ([f39d538](https://github.com/mohamed-rekiba/forge-search/commit/f39d5389d7e5d32ccae1413e01233c551d43da0e))
* per-file summary chunks — semantic recall 0% -&gt; 50%, hybrid 25% -&gt; 62.5% ([1c0f4cc](https://github.com/mohamed-rekiba/forge-search/commit/1c0f4cccfd5b01fab4b58e497b028bd3394cadda))
* print the config to stderr again — the only channel that reaches the client ([9ece5fd](https://github.com/mohamed-rekiba/forge-search/commit/9ece5fd4a27cabf283e5c3938f4b2e7a8b5e901f))
* serve crashed on startup — ForgeSearch never got the index_dir field ([7a93577](https://github.com/mohamed-rekiba/forge-search/commit/7a93577025b6434443b83a37c34431d3e790980b))
* serve is silent on stderr, and names the active adapter ([b01fa90](https://github.com/mohamed-rekiba/forge-search/commit/b01fa9084912b75d495640360d101111fc6532dc))
* stop .gitignore silently excluding the benchmark fixture ([7bf3f40](https://github.com/mohamed-rekiba/forge-search/commit/7bf3f40b3605d084d3796a17a9bea3b3c40ef17f))
* stop recommending ${workspaceFolder}, and isolate the test cache ([64f9167](https://github.com/mohamed-rekiba/forge-search/commit/64f9167332c2a267ab7f150f847fec4a2dc15100))
* stop reporting an adapter expansion that expanded nothing ([bd772ef](https://github.com/mohamed-rekiba/forge-search/commit/bd772ef460340ca353cf30cd0181fea32ec29a5e))


### Refactors

* drop the instructions-field workaround, keep the two real channels ([97abf4d](https://github.com/mohamed-rekiba/forge-search/commit/97abf4d17ad1cdcb3abc29b1c9b2f07ac9696524))
* explicit roots and explicit adapters — remove all discovery ([06fbbef](https://github.com/mohamed-rekiba/forge-search/commit/06fbbef41a8971c4f56c224a83ffd31444eae160))


### Documentation

* label the remaining code fences ([a2459fb](https://github.com/mohamed-rekiba/forge-search/commit/a2459fb730fa1e78f6fdd6a1c77a222394720124))
* README and BENCHMARK caught up with reality ([c7a1af8](https://github.com/mohamed-rekiba/forge-search/commit/c7a1af8ab6d9cad087e709108198fd83b1580dfa))
* README covers filename matching, bench, warm, and schema versioning ([4b6afb4](https://github.com/mohamed-rekiba/forge-search/commit/4b6afb4c070f156d5211576bf39c27e8c76a1991))
* record the semantic benchmark — 0% recall on a real corpus ([23bb457](https://github.com/mohamed-rekiba/forge-search/commit/23bb457707cd0eb360fc056f957b440d41145497))
