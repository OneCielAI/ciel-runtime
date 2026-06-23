#!/usr/bin/env node
"use strict";

process.env.CLAUDE_ANY_NPM_MODE = "cli";
process.argv.push("stop");
require("./run-claude-any");
