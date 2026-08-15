import { authServerMetadataHandlerClerk, metadataCorsOptionsRequestHandler } from "@clerk/mcp-tools/next";

const handler = authServerMetadataHandlerClerk();

export { handler as GET };
export const OPTIONS = metadataCorsOptionsRequestHandler();
