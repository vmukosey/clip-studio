import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { validateProject } from '../src/lib/validate';

describe('clip handoff', () => {
  it('opens the generated editable project', () => {
    const path = process.env.CLIP_PROJECT_PATH;
    if (!path) throw new Error('CLIP_PROJECT_PATH is required');
    const project = JSON.parse(readFileSync(path, 'utf8'));
    const result = validateProject(project);
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(project.width).toBe(1080);
    expect(project.height).toBe(1920);
    expect(project.media).toHaveLength(2);
    expect(project.clips[0].offset).toBeGreaterThan(0);
  });
});
