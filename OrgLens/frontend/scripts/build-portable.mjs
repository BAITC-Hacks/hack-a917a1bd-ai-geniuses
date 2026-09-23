// Сборка в текущем процессе Node.js: TypeScript вместо сервиса esbuild.
// Использует установленные зависимости и не запускает дочерние процессы.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import ts from 'typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const configPath = path.join(root, 'tsconfig.json');
const read = ts.readConfigFile(configPath, ts.sys.readFile);
const parsed = read.error ? null : ts.parseJsonConfigFileContent(read.config, ts.sys, root);
const diagnostics = read.error ? [read.error] : [
  ...parsed.errors,
  ...ts.getPreEmitDiagnostics(ts.createProgram(parsed.fileNames, { ...parsed.options, noEmit: true })),
];
if (diagnostics.length) {
  console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCurrentDirectory: () => root,
    getCanonicalFileName: filename => filename,
    getNewLine: () => '\n',
  }));
  process.exitCode = 1;
} else {
  console.log('Проверка TypeScript завершена без ошибок.');
  await build({
    root,
    configFile: false,
    envFile: false,
    esbuild: false,
    // Обычный resolver Vite на Windows запускает команду проверки сетевых дисков.
    // Сохраняем локальные пути пакетов без этой необязательной оптимизации.
    resolve: { preserveSymlinks: true },
    // Макрос NODE_ENV преобразует AST ниже; vite:define не нужен для React.
    keepProcessEnv: true,
    plugins: [{
      name: 'orglens-typescript-in-process',
      enforce: 'pre',
      transform(source, id) {
        if (!/\.[cm]?[jt]sx?(?:\?.*)?$/.test(id)) return null;
        const result = ts.transpileModule(source, {
          fileName: id.split('?')[0],
          compilerOptions: {
            target: ts.ScriptTarget.ESNext,
            module: ts.ModuleKind.ESNext,
            jsx: ts.JsxEmit.ReactJSX,
            isolatedModules: true,
            sourceMap: false,
          },
          transformers: { before: [context => sourceFile => {
            const visit = node => {
              if (ts.isPropertyAccessExpression(node) && node.name.text === 'NODE_ENV'
                  && ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'env'
                  && ts.isIdentifier(node.expression.expression) && node.expression.expression.text === 'process') {
                return ts.factory.createStringLiteral('production');
              }
              return ts.visitEachChild(node, visit, context);
            };
            return ts.visitNode(sourceFile, visit);
          }] },
        });
        return { code: result.outputText, map: null };
      },
    }],
    build: {
      outDir: path.join(root, 'dist'),
      target: 'esnext',
      minify: false,
      cssMinify: false,
    },
  });
}
