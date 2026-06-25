#!/usr/bin/env node
"use strict";

process.env.CIEL_RUNTIME_NPM_MODE = "cli";
process.argv.push("stop");
require("./run-ciel-runtime");
