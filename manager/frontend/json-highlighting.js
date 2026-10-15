import {HighlightStyle, StreamLanguage, syntaxHighlighting} from '@codemirror/language';
import {json} from '@codemirror/legacy-modes/mode/javascript';
import {tags} from '@lezer/highlight';

// The legacy JSONC tokenizer calls true/false/null "atom". Map them explicitly
// instead of expecting the modern bool/null tags to be emitted automatically.
export const jsonLanguage = StreamLanguage.define({
  ...json,
  token(stream, state) {
    const token = json.token(stream, state);
    const value = stream.current();
    if (token === 'atom') {
      if (value === 'true' || value === 'false') return 'jsonBoolean';
      if (value === 'null') return 'jsonNull';
    }
    if (token === 'operator' && value === '-' && /\d/.test(stream.peek() || '')) return 'jsonNumber';
    if (token?.includes('string') && /^\s*:/.test(stream.string.slice(stream.pos))) return 'jsonKey';
    if (!token && /^[{}\[\]]$/.test(value)) return 'jsonBracket';
    if (!token && /^[:,]$/.test(value)) return 'jsonSeparator';
    return token;
  },
  tokenTable: {
    jsonKey: tags.propertyName,
    jsonNumber: tags.number,
    jsonBoolean: tags.bool,
    jsonNull: tags.null,
    jsonBracket: tags.bracket,
    jsonSeparator: tags.separator,
  },
});

// Shared semantic colors across config, history, diff, and skill file editors.
export const jsonHighlightStyles = [
  {tag: tags.propertyName, color: '#2563a6'},
  {tag: tags.string, color: '#24764f'},
  {tag: tags.number, color: '#8554b3'},
  {tag: tags.bool, color: '#c44732', fontWeight: '600'},
  {tag: tags.null, color: '#7d6591', fontStyle: 'italic'},
  {tag: tags.comment, color: '#7a8493', fontStyle: 'italic'},
  {tag: tags.bracket, color: '#51657c'},
  {tag: tags.separator, color: '#8892a3'},
];
export const jsonHighlighting = syntaxHighlighting(HighlightStyle.define(jsonHighlightStyles));
