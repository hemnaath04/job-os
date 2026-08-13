/**
 * Project the verified profile onto canonical field keys.
 *
 * This is the whole answer set. If a key is absent from the map the extension
 * has nothing verbatim to say and the field is left blank, which is the
 * intended outcome far more often than not. Nothing here composes prose,
 * summarizes, or infers: every entry is a value that already existed in the
 * vault, plus a citation pointing at the fact it came from.
 *
 * Fact shapes come from apps/api/src/job_os/services/profile_import.py:
 *   contact       payload: name, email, phone, url, address, city, region,
 *                          postalCode, countryCode, profiles{network: url}
 *   education     org = institution, payload: studyType, area, score
 *   experience    org = company, title = position
 *   authorization payload: work_authorized, requires_sponsorship
 *   eeo           payload: gender, race, hispanic, veteran, disability
 */
import { firstFactOfKind, type VerifiedProfile } from "./profile.ts";
import { sourced, sourcedDate, sourcedSubstring } from "./provenance.ts";
import { EEO_FIELD_KEYS, type EeoConsent, type FieldKey, type SourcedValue } from "./types.ts";

export type ProfileValues = ReadonlyMap<FieldKey, SourcedValue>;

/**
 * Build the key-to-value map.
 *
 * `consent` gates the demographic keys one at a time. A key the user has not
 * explicitly turned on never enters the map, so the filler cannot reach it even
 * by accident.
 */
export function buildProfileValues(
  profile: VerifiedProfile,
  consent: EeoConsent = {},
): ProfileValues {
  const values = new Map<FieldKey, SourcedValue>();
  const put = (key: FieldKey, value: SourcedValue | null): void => {
    if (value && !values.has(key)) values.set(key, value);
  };

  const contact = firstFactOfKind(profile, "contact");
  if (contact) {
    const p = contact.payload;
    const fullName = typeof p.name === "string" ? p.name : contact.title;

    put("full_name", sourced(contact, "payload.name", fullName));
    // Splitting a name keeps both halves literally present in the source, which
    // `sourcedSubstring` verifies. A one-word name yields no last name, and a
    // blank last name is better than a duplicated first one.
    const parts = fullName.trim().split(/\s+/);
    if (parts.length >= 2) {
      put("first_name", sourcedSubstring(contact, "payload.name", fullName, parts[0]!));
      put("last_name", sourcedSubstring(contact, "payload.name", fullName, parts[parts.length - 1]!));
    } else if (parts.length === 1) {
      put("first_name", sourcedSubstring(contact, "payload.name", fullName, parts[0]!));
    }

    put("email", sourced(contact, "payload.email", p.email));
    put("phone", sourced(contact, "payload.phone", p.phone));
    put("address_line1", sourced(contact, "payload.address", p.address));
    put("city", sourced(contact, "payload.city", p.city));
    put("state", sourced(contact, "payload.region", p.region));
    put("postal_code", sourced(contact, "payload.postalCode", p.postalCode));
    put("country", sourced(contact, "payload.countryCode", p.countryCode));
    put("portfolio_url", sourced(contact, "payload.url", p.url));

    const profiles = isRecord(p.profiles) ? p.profiles : {};
    put("linkedin_url", sourced(contact, "payload.profiles.linkedin", profiles.linkedin));
    put("github_url", sourced(contact, "payload.profiles.github", profiles.github));
  }

  const education = firstFactOfKind(profile, "education");
  if (education) {
    const p = education.payload;
    put("school", sourced(education, "org", education.org));
    put("degree", sourced(education, "payload.studyType", p.studyType));
    put("field_of_study", sourced(education, "payload.area", p.area));
    put("gpa", sourced(education, "payload.score", p.score));
    put("education_start", sourcedDate(education, "start_date", education.startDate, "mm/yyyy"));
    put("education_end", sourcedDate(education, "end_date", education.endDate, "mm/yyyy"));
  }

  const experience = firstFactOfKind(profile, "experience");
  if (experience) {
    put("current_company", sourced(experience, "org", experience.org));
    put("current_title", sourced(experience, "title", experience.title));
    put("work_start", sourcedDate(experience, "start_date", experience.startDate, "mm/yyyy"));
    put("work_end", sourcedDate(experience, "end_date", experience.endDate, "mm/yyyy"));
  }

  // Work authorization is a legal answer, so it only comes from a fact the user
  // created and confirmed. There is no inference from citizenship, location, or
  // school: a wrong answer here is not a typo, it is a misrepresentation.
  const authorization = firstFactOfKind(profile, "authorization");
  if (authorization) {
    const p = authorization.payload;
    put("work_authorized", sourced(authorization, "payload.work_authorized", p.work_authorized));
    put(
      "requires_sponsorship",
      sourced(authorization, "payload.requires_sponsorship", p.requires_sponsorship),
    );
  }

  // Demographic answers, one consent check per key.
  const eeo = firstFactOfKind(profile, "eeo");
  if (eeo) {
    const p = eeo.payload;
    const eeoPairs: ReadonlyArray<readonly [FieldKey, string]> = [
      ["eeo_gender", "gender"],
      ["eeo_race", "race"],
      ["eeo_hispanic", "hispanic"],
      ["eeo_veteran", "veteran"],
      ["eeo_disability", "disability"],
    ];
    for (const [key, attr] of eeoPairs) {
      if (consent[key] !== true) continue;
      put(key, sourced(eeo, `payload.${attr}`, p[attr]));
    }
  }

  return values;
}

/** True when the key is demographic and the user has not opted it in. Kept
 * separate from the map so the planner can explain the blank rather than
 * silently omitting it. */
export function isBlockedByConsent(key: FieldKey, consent: EeoConsent): boolean {
  return EEO_FIELD_KEYS.has(key) && consent[key] !== true;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
