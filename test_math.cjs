const assert=require('node:assert/strict');
const md=require('./vendor/markdown-it.min.js')({html:false});
require('./math.js')(md,require('./vendor/katex/katex.min.js'));
for(const text of ['公式 $x^2+y^2$','\\(H(s)=\\frac{s}{s^2+4s+1}\\)','\\[\\int_0^1 x\\,dx=\\frac12\\]','$$a^2+b^2=c^2$$'])assert.match(md.render(text),/class="katex"/);
assert.doesNotMatch(md.render('```text\n$x^2$\n```'),/class="katex"/);
assert.doesNotMatch(md.render('$\\href{javascript:alert(1)}{x}$'),/href="javascript:/);
assert.match(md.render('价格 $20 和 $30'),/价格 \$20 和 \$30/);
console.log('Math rendering: inline, display, TeX delimiters, code exclusion and unsafe links passed');
