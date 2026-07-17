package org.pyelk.oracle;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import java.util.TreeMap;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

/** JSON-lines process boundary for the quarantined ELK reference oracle. */
public final class Main {

    private static final ObjectMapper JSON = new ObjectMapper()
        .enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
        .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS);

    private Main() {}

    public static void main(String[] arguments) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        PrintStream protocolOutput = System.out;
        System.setOut(new PrintStream(OutputStream.nullOutputStream(), true, StandardCharsets.UTF_8));
        try (BufferedReader input = new BufferedReader(new InputStreamReader(
                System.in, StandardCharsets.UTF_8));
             BufferedWriter output = new BufferedWriter(new OutputStreamWriter(
                protocolOutput, StandardCharsets.UTF_8))) {
            String line;
            while ((line = input.readLine()) != null) {
                if (line.isBlank()) {
                    continue;
                }
                output.write(handle(line));
                output.newLine();
                output.flush();
            }
        }
    }

    private static String handle(String line) throws JsonProcessingException {
        String id = null;
        Map<String, Object> response = new TreeMap<>();
        response.put("schema", 1);
        try {
            JsonNode request = JSON.readTree(line);
            if (request == null || !request.isObject()
                    || request.path("schema").asInt(-1) != 1) {
                throw new Oracle.OracleException("invalid_request");
            }
            JsonNode idNode = request.get("id");
            if (idNode == null || !idNode.isTextual() || idNode.textValue().isEmpty()) {
                throw new Oracle.OracleException("invalid_request");
            }
            id = idNode.textValue();
            Oracle.Result result = Oracle.execute(request);
            response.put("complete", result.complete);
            response.put("diagnostics", result.diagnostics);
            response.put("features", result.features);
            response.put("ok", true);
            response.put("value", result.value);
        } catch (Oracle.OracleException error) {
            response.put("diagnostics", List.of());
            response.put("error", Map.of("category", error.category, "span", Map.of()));
            response.put("ok", false);
        } catch (Exception error) {
            if ("1".equals(System.getenv("PYELK_ORACLE_DEBUG"))) {
                error.printStackTrace(System.err);
            }
            response.put("diagnostics", List.of());
            response.put("error", Map.of("category", category(error), "span", Map.of()));
            response.put("ok", false);
        }
        if (id != null) {
            response.put("id", id);
        }
        return JSON.writeValueAsString(response);
    }

    private static String category(Exception error) {
        if (error instanceof java.nio.file.NoSuchFileException) {
            return "input_not_found";
        }
        if (error instanceof java.io.IOException) {
            return "io_error";
        }
        String name = error.getClass().getName();
        if (name.contains("Parse") || name.contains("Parser")) {
            return "parse_error";
        }
        if (name.contains("TestResultComparison")) {
            return "golden_mismatch";
        }
        return "oracle_failure";
    }
}
