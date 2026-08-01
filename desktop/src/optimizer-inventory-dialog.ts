import path from 'node:path';

import type { OpenDialogOptions } from 'electron';

import type {
  OptimizerInventoryImportResult,
  OptimizerInventorySelectionResult,
} from './shared/optimizer-inventory';

interface FileSelection {
  canceled: boolean;
  filePaths: string[];
}

type FilePicker = (options: OpenDialogOptions) => Promise<FileSelection>;
type InventoryImporter = (sourcePath: string) => Promise<OptimizerInventoryImportResult>;

export function optimizerInventoryDialogOptions(): OpenDialogOptions {
  return {
    title: 'Import Fribbels gear.txt',
    buttonLabel: 'Import owned gear',
    properties: ['openFile'],
    filters: [{ name: 'Fribbels gear.txt', extensions: ['txt'] }],
  };
}

export class OptimizerInventoryImportCoordinator {
  private busy = false;

  constructor(
    private readonly pickFile: FilePicker,
    private readonly importFile: InventoryImporter,
  ) {}

  async run(): Promise<OptimizerInventorySelectionResult> {
    if (this.busy) {
      throw new Error('An inventory import is already in progress.');
    }
    this.busy = true;
    try {
      const selection = await this.pickFile(optimizerInventoryDialogOptions());
      if (selection.canceled || selection.filePaths.length === 0) {
        return { outcome: 'cancelled' };
      }
      if (selection.filePaths.length !== 1
        || path.extname(selection.filePaths[0]).toLowerCase() !== '.txt') {
        throw new Error('Choose a Fribbels gear.txt file.');
      }
      return { outcome: 'imported', import: await this.importFile(selection.filePaths[0]) };
    } finally {
      this.busy = false;
    }
  }
}
