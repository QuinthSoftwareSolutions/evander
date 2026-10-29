// Keep the real browser and recorder while independently delaying their startup.
import { pathToFileURL } from "node:url";

const actual = await import(pathToFileURL(process.env.DEMO_BASE_LAUNCH_HELPER).href);
export const launchSeededChromium = actual.launchSeededChromium;

export async function createManagedWebsiteContext(...args) {
  const context = await actual.createManagedWebsiteContext(...args);
  const newPage = context.newPage.bind(context);
  context.newPage = async (...pageArgs) => {
    await new Promise(resolve => setTimeout(resolve, Number(process.env.DEMO_PAGE_CREATION_DELAY_MS)));
    const page = await newPage(...pageArgs);
    const start = page.screencast.start.bind(page.screencast);
    page.screencast.start = async (...options) => {
      await new Promise(resolve => setTimeout(resolve, Number(process.env.DEMO_RECORDING_START_DELAY_MS)));
      return start(...options);
    };
    return page;
  };
  return context;
}
