import type { TestProject } from "vitest/node";
import { createTemplate, dropDatabase } from "./test-db";

export default async function setup(project: TestProject) {
  const template = await createTemplate();
  project.provide("template", template);
  return () => dropDatabase(template);
}
