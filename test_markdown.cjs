const assert = require('node:assert/strict');
const MarkdownIt = require('./vendor/markdown-it.min.js');
const md = new MarkdownIt({html:false, breaks:true, linkify:true});
const html = md.render('# 标题\n\n**粗体** 和 `code`\n\n- 第一项\n- 第二项\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n\n> 引用\n\n```js\nconst a = 1;\n```\n\n[链接](https://example.com)');
for (const tag of ['h1','strong','code','ul','table','blockquote','pre','a']) assert.match(html,new RegExp('<'+tag+'[ >]'));
const unsafe = md.render('<img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))');
assert.doesNotMatch(unsafe,/<(?:img|script)\b/i);
assert.doesNotMatch(unsafe,/href="javascript:/i);
console.log('Markdown formatting and unsafe HTML/link checks passed');
